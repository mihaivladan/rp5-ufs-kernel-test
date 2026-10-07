// SPDX-License-Identifier: GPL-2.0-only
/*
 * Minimal initramfs framebuffer splash renderer.
 *
 * Branding remains an external /flash asset.  This helper contains no vendor
 * or product artwork; it validates and renders an uncompressed 24-bit BMP.
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

	if (argc != 2) {
		fprintf(stderr, "usage: %s IMAGE.bmp\n", argv[0]);
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
