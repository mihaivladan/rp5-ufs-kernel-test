// SPDX-License-Identifier: GPL-2.0-only
/*
 * Minimal initramfs framebuffer handoff and splash renderer.
 *
 * The --handoff mode snapshots the framebuffer inherited from firmware and
 * redraws it as soon as the Qualcomm DRM fbdev replaces simpledrm.  This keeps
 * all vendor and product artwork outside the public kernel source and build
 * artifact.  The legacy IMAGE.bmp mode validates and renders an external
 * uncompressed 24-bit BMP.
 */

#include <errno.h>
#include <fcntl.h>
#include <linux/fb.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define POLL_NS 20000000L
#define CAPTURE_ATTEMPTS 250
#define HANDOFF_ATTEMPTS 500

struct framebuffer {
	struct fb_fix_screeninfo fix;
	struct fb_var_screeninfo var;
	uint8_t *mapping;
	size_t mapping_size;
	int fd;
};

struct __attribute__((packed)) bmp_header {
	uint16_t magic;
	uint32_t file_size;
	uint16_t reserved1;
	uint16_t reserved2;
	uint32_t pixel_offset;
	uint32_t dib_size;
	int32_t width;
	int32_t height;
	uint16_t planes;
	uint16_t bits_per_pixel;
	uint32_t compression;
	uint32_t image_size;
	int32_t x_pixels_per_m;
	int32_t y_pixels_per_m;
	uint32_t colors_used;
	uint32_t colors_important;
};

static uint32_t channel(uint8_t value, const struct fb_bitfield *field)
{
	uint32_t maximum;

	if (!field->length)
		return 0;
	maximum = (1U << field->length) - 1;
	return ((uint32_t)value * maximum / 255U) << field->offset;
}

static uint32_t pixel_value(uint8_t red, uint8_t green, uint8_t blue,
			    const struct fb_var_screeninfo *var)
{
	return channel(red, &var->red) | channel(green, &var->green) |
	       channel(blue, &var->blue);
}

static void store_pixel(uint8_t *destination, uint32_t value,
			unsigned int bytes_per_pixel)
{
	unsigned int index;

	for (index = 0; index < bytes_per_pixel; index++)
		destination[index] = value >> (index * 8);
}

static uint8_t extract_channel(uint32_t value, const struct fb_bitfield *field)
{
	uint32_t maximum;

	if (!field->length)
		return 0;
	maximum = (1U << field->length) - 1;
	return (uint8_t)((((value >> field->offset) & maximum) * 255U) /
			 maximum);
}

static uint32_t load_pixel(const uint8_t *source, unsigned int bytes_per_pixel)
{
	uint32_t value = 0;
	unsigned int index;

	for (index = 0; index < bytes_per_pixel; index++)
		value |= (uint32_t)source[index] << (index * 8);
	return value;
}

static void close_framebuffer(struct framebuffer *fb)
{
	if (fb->mapping != MAP_FAILED)
		munmap(fb->mapping, fb->mapping_size);
	if (fb->fd >= 0)
		close(fb->fd);
	fb->mapping = MAP_FAILED;
	fb->fd = -1;
}

static int open_framebuffer(const char *path, struct framebuffer *fb)
{
	memset(fb, 0, sizeof(*fb));
	fb->fd = -1;
	fb->mapping = MAP_FAILED;
	fb->fd = open(path, O_RDWR | O_CLOEXEC);
	if (fb->fd < 0 || ioctl(fb->fd, FBIOGET_FSCREENINFO, &fb->fix) < 0 ||
	    ioctl(fb->fd, FBIOGET_VSCREENINFO, &fb->var) < 0)
		goto error;
	fb->mapping_size = fb->fix.smem_len;
	if (!fb->mapping_size)
		fb->mapping_size =
			(size_t)fb->fix.line_length * fb->var.yres_virtual;
	if (!fb->mapping_size)
		goto error;
	fb->mapping = mmap(NULL, fb->mapping_size, PROT_READ | PROT_WRITE,
			   MAP_SHARED, fb->fd, 0);
	if (fb->mapping == MAP_FAILED)
		goto error;
	return 0;

error:
	close_framebuffer(fb);
	return -1;
}

static int usable_format(const struct framebuffer *fb)
{
	unsigned int bytes_per_pixel = (fb->var.bits_per_pixel + 7U) / 8U;

	return bytes_per_pixel >= 2 && bytes_per_pixel <= 4 &&
	       fb->var.xres && fb->var.yres && fb->fix.line_length;
}

static int is_msm_framebuffer(const struct framebuffer *fb)
{
	return !memcmp(fb->fix.id, "msm", 3);
}

static void log_marker(FILE *log, const char *marker,
		       const struct framebuffer *fb)
{
	struct timespec now;

	if (!log)
		return;
	clock_gettime(CLOCK_MONOTONIC, &now);
	fprintf(log, "%lld.%03ld %s id=%.16s %ux%u-%u\n",
		(long long)now.tv_sec, now.tv_nsec / 1000000L, marker,
		fb ? fb->fix.id : "none", fb ? fb->var.xres : 0,
		fb ? fb->var.yres : 0, fb ? fb->var.bits_per_pixel : 0);
	fflush(log);
}

static int capture_rgb(const struct framebuffer *fb, uint8_t **rgb_out)
{
	unsigned int bytes_per_pixel = (fb->var.bits_per_pixel + 7U) / 8U;
	size_t pixels_count = (size_t)fb->var.xres * fb->var.yres;
	uint8_t *rgb;
	unsigned int x;
	unsigned int y;

	if (!usable_format(fb) || pixels_count > SIZE_MAX / 3U)
		return -1;
	rgb = malloc(pixels_count * 3U);
	if (!rgb)
		return -1;
	for (y = 0; y < fb->var.yres; y++) {
		const uint8_t *row = fb->mapping +
			(size_t)(y + fb->var.yoffset) * fb->fix.line_length +
			(size_t)fb->var.xoffset * bytes_per_pixel;
		for (x = 0; x < fb->var.xres; x++) {
			uint32_t value = load_pixel(row + (size_t)x * bytes_per_pixel,
						    bytes_per_pixel);
			size_t output = ((size_t)y * fb->var.xres + x) * 3U;
			rgb[output] = extract_channel(value, &fb->var.red);
			rgb[output + 1] = extract_channel(value, &fb->var.green);
			rgb[output + 2] = extract_channel(value, &fb->var.blue);
		}
	}
	*rgb_out = rgb;
	return 0;
}

static int draw_rgb(struct framebuffer *fb, const uint8_t *rgb,
		    unsigned int width, unsigned int height)
{
	unsigned int bytes_per_pixel = (fb->var.bits_per_pixel + 7U) / 8U;
	unsigned int copy_width;
	unsigned int copy_height;
	unsigned int source_x;
	unsigned int source_y;
	unsigned int destination_x;
	unsigned int destination_y;
	unsigned int x;
	unsigned int y;
	int unblank = FB_BLANK_UNBLANK;

	if (!usable_format(fb))
		return -1;
	copy_width = width < fb->var.xres ? width : fb->var.xres;
	copy_height = height < fb->var.yres ? height : fb->var.yres;
	source_x = (width - copy_width) / 2U;
	source_y = (height - copy_height) / 2U;
	destination_x = (fb->var.xres - copy_width) / 2U;
	destination_y = (fb->var.yres - copy_height) / 2U;
	memset(fb->mapping, 0, fb->mapping_size);
	for (y = 0; y < copy_height; y++) {
		uint8_t *row = fb->mapping +
			(size_t)(destination_y + y + fb->var.yoffset) *
				fb->fix.line_length +
			(size_t)(destination_x + fb->var.xoffset) *
				bytes_per_pixel;
		for (x = 0; x < copy_width; x++) {
			size_t input = ((size_t)(source_y + y) * width +
					(source_x + x)) * 3U;
			uint32_t value = pixel_value(rgb[input], rgb[input + 1],
						   rgb[input + 2], &fb->var);
			store_pixel(row + (size_t)x * bytes_per_pixel, value,
				    bytes_per_pixel);
		}
	}
	msync(fb->mapping, fb->mapping_size, MS_SYNC);
	ioctl(fb->fd, FBIOBLANK, unblank);
	return 0;
}

static int handoff_firmware_frame(void)
{
	struct timespec delay = { .tv_nsec = POLL_NS };
	struct framebuffer source;
	struct framebuffer target;
	uint8_t *rgb = NULL;
	FILE *log;
	int attempt;
	int result = 1;

	log = fopen("/run/consoleos-early-display.log", "w");
	log_marker(log, "start", NULL);
	for (attempt = 0; attempt < CAPTURE_ATTEMPTS; attempt++) {
		if (!open_framebuffer("/dev/fb0", &source)) {
			if (!is_msm_framebuffer(&source) && usable_format(&source) &&
			    !capture_rgb(&source, &rgb)) {
				log_marker(log, "captured-firmware", &source);
				close_framebuffer(&source);
				break;
			}
			close_framebuffer(&source);
		}
		nanosleep(&delay, NULL);
	}
	if (!rgb)
		goto out;

	for (attempt = 0; attempt < HANDOFF_ATTEMPTS; attempt++) {
		int index;

		for (index = 0; index < 4; index++) {
			char path[16];

			snprintf(path, sizeof(path), "/dev/fb%d", index);
			if (open_framebuffer(path, &target))
				continue;
			if (is_msm_framebuffer(&target)) {
				if (!draw_rgb(&target, rgb, source.var.xres,
					      source.var.yres)) {
					log_marker(log, "redrawn-msm", &target);
					result = 0;
				}
				close_framebuffer(&target);
				goto out;
			}
			close_framebuffer(&target);
		}
		nanosleep(&delay, NULL);
	}

out:
	if (result)
		log_marker(log, "failed", NULL);
	if (log)
		fclose(log);
	free(rgb);
	return result;
}

static int wait_for_fb(void)
{
	struct timespec delay = { .tv_nsec = 100000000 };
	int attempts;
	int fd;

	for (attempts = 0; attempts < 50; attempts++) {
		fd = open("/dev/fb0", O_RDWR | O_CLOEXEC);
		if (fd >= 0)
			return fd;
		nanosleep(&delay, NULL);
	}
	return -1;
}

int main(int argc, char **argv)
{
	struct fb_fix_screeninfo fix;
	struct fb_var_screeninfo var;
	struct bmp_header bmp;
	struct stat status;
	uint8_t *framebuffer = MAP_FAILED;
	uint8_t *pixels = NULL;
	uint8_t *row;
	size_t mapping_size;
	size_t row_size;
	unsigned int bytes_per_pixel;
	int fb = -1;
	int image = -1;
	int top_down;
	int x_origin;
	int y_origin;
	int x;
	int y;
	int result = 1;

	if (argc == 2 && !strcmp(argv[1], "--handoff"))
		return handoff_firmware_frame();
	if (argc != 2) {
		fprintf(stderr, "usage: %s --handoff | IMAGE.bmp\n", argv[0]);
		return 2;
	}

	image = open(argv[1], O_RDONLY | O_CLOEXEC);
	if (image < 0 || fstat(image, &status) < 0 ||
	    read(image, &bmp, sizeof(bmp)) != (ssize_t)sizeof(bmp))
		goto out;
	if (bmp.magic != 0x4d42 || bmp.dib_size < 40 || bmp.width <= 0 ||
	    bmp.height == 0 || bmp.planes != 1 || bmp.bits_per_pixel != 24 ||
	    bmp.compression != 0)
		goto out;

	top_down = bmp.height < 0;
	if (bmp.height < 0)
		bmp.height = -bmp.height;
	row_size = ((size_t)bmp.width * 3U + 3U) & ~3U;
	if (row_size > SIZE_MAX / (size_t)bmp.height ||
	    bmp.pixel_offset > (uint32_t)status.st_size ||
	    row_size * (size_t)bmp.height >
		(size_t)status.st_size - bmp.pixel_offset)
		goto out;
	pixels = malloc(row_size * (size_t)bmp.height);
	if (!pixels || lseek(image, bmp.pixel_offset, SEEK_SET) < 0 ||
	    read(image, pixels, row_size * (size_t)bmp.height) !=
		(ssize_t)(row_size * (size_t)bmp.height))
		goto out;

	fb = wait_for_fb();
	if (fb < 0 || ioctl(fb, FBIOGET_FSCREENINFO, &fix) < 0 ||
	    ioctl(fb, FBIOGET_VSCREENINFO, &var) < 0)
		goto out;
	bytes_per_pixel = (var.bits_per_pixel + 7U) / 8U;
	if (bytes_per_pixel < 2 || bytes_per_pixel > 4 ||
	    bmp.width > (int32_t)var.xres || bmp.height > (int32_t)var.yres)
		goto out;
	mapping_size = fix.smem_len;
	if (!mapping_size)
		mapping_size = (size_t)fix.line_length * var.yres_virtual;
	framebuffer = mmap(NULL, mapping_size, PROT_READ | PROT_WRITE,
			   MAP_SHARED, fb, 0);
	if (framebuffer == MAP_FAILED)
		goto out;

	memset(framebuffer, 0, mapping_size);
	x_origin = ((int)var.xres - bmp.width) / 2;
	y_origin = ((int)var.yres - bmp.height) / 2;
	for (y = 0; y < bmp.height; y++) {
		int source_y = top_down ? y : bmp.height - 1 - y;
		uint8_t *destination = framebuffer +
			(size_t)(y_origin + y + var.yoffset) * fix.line_length +
			(size_t)(x_origin + var.xoffset) * bytes_per_pixel;
		row = pixels + (size_t)source_y * row_size;
		for (x = 0; x < bmp.width; x++) {
			uint8_t blue = row[x * 3];
			uint8_t green = row[x * 3 + 1];
			uint8_t red = row[x * 3 + 2];
			store_pixel(destination + (size_t)x * bytes_per_pixel,
				    pixel_value(red, green, blue, &var),
				    bytes_per_pixel);
		}
	}
	result = 0;

out:
	if (framebuffer != MAP_FAILED)
		munmap(framebuffer, mapping_size);
	free(pixels);
	if (fb >= 0)
		close(fb);
	if (image >= 0)
		close(image);
	if (result)
		fprintf(stderr, "consoleos-early-splash: %s\n", strerror(errno));
	return result;
}
