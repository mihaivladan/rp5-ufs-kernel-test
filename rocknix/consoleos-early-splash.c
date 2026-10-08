// SPDX-License-Identifier: GPL-2.0-only
/*
 * Minimal initramfs framebuffer handoff and splash renderer.
 *
 * The --handoff mode inspects the framebuffer inherited from firmware, then
 * renders the generated REDIKA device asset as soon as the Qualcomm DRM fbdev
 * replaces simpledrm.  It also forces the fbdev mode/pan/unblank path and logs
 * every return value.  A successful memcpy is deliberately not treated as a
 * visible handoff.  The legacy IMAGE.bmp mode validates and renders an
 * external uncompressed 24-bit BMP.
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
#define LOGO_ASSET "/usr/share/consoleos/boot-display/logo.bgra"

#ifndef CONSOLEOS_NATIVE_WIDTH
#error CONSOLEOS_NATIVE_WIDTH must come from generated device.cfg
#endif
#ifndef CONSOLEOS_NATIVE_HEIGHT
#error CONSOLEOS_NATIVE_HEIGHT must come from generated device.cfg
#endif
#ifndef CONSOLEOS_LOGO_X
#error CONSOLEOS_LOGO_X must come from generated device.cfg
#endif
#ifndef CONSOLEOS_LOGO_Y
#error CONSOLEOS_LOGO_Y must come from generated device.cfg
#endif
#ifndef CONSOLEOS_LOGO_WIDTH
#error CONSOLEOS_LOGO_WIDTH must come from generated device.cfg
#endif
#ifndef CONSOLEOS_LOGO_HEIGHT
#error CONSOLEOS_LOGO_HEIGHT must come from generated device.cfg
#endif

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

struct __attribute__((packed)) logo_header {
	uint8_t magic[8];
	uint32_t header_bytes;
	uint32_t width;
	uint32_t height;
	uint32_t format;
	uint32_t payload_bytes;
	uint8_t payload_sha256[32];
	uint32_t reserved;
};

struct activation_result {
	int put_rc;
	int put_errno;
	int sync_rc;
	int sync_errno;
	int pan_rc;
	int pan_errno;
	int unblank_rc;
	int unblank_errno;
	int get_rc;
	int get_errno;
	int mode_matches;
	int connector_enabled;
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

static void log_capture_stats(FILE *log, const uint8_t *rgb,
			      unsigned int width, unsigned int height)
{
	uint64_t checksum = 1469598103934665603ULL;
	uint64_t nonblack = 0;
	size_t size = (size_t)width * height * 3U;
	size_t index;

	if (!log || !rgb)
		return;
	for (index = 0; index < size; index++) {
		checksum ^= rgb[index];
		checksum *= 1099511628211ULL;
		if (rgb[index])
			nonblack++;
	}
	fprintf(log,
		"firmware-pixels nonzero_channels=%llu/%llu fnv1a64=%016llx\n",
		(unsigned long long)nonblack, (unsigned long long)size,
		(unsigned long long)checksum);
	fflush(log);
}

static void log_activation(FILE *log, const struct activation_result *result)
{
	if (!log || !result)
		return;
	fprintf(log,
		"fbdev-activation put=%d:%d msync=%d:%d pan=%d:%d "
		"unblank=%d:%d get=%d:%d mode_matches=%d connector_enabled=%d\n",
		result->put_rc, result->put_errno,
		result->sync_rc, result->sync_errno,
		result->pan_rc, result->pan_errno,
		result->unblank_rc, result->unblank_errno,
		result->get_rc, result->get_errno, result->mode_matches,
		result->connector_enabled);
	fflush(log);
}

static int file_has_prefix(const char *path, const char *expected)
{
	char value[32];
	ssize_t length;
	int fd;

	fd = open(path, O_RDONLY | O_CLOEXEC);
	if (fd < 0)
		return 0;
	length = read(fd, value, sizeof(value) - 1);
	close(fd);
	if (length <= 0)
		return 0;
	value[length] = '\0';
	return !strncmp(value, expected, strlen(expected));
}

static int dsi_connector_enabled(void)
{
	char path[80];
	int card;

	for (card = 0; card < 4; card++) {
		snprintf(path, sizeof(path),
			 "/sys/class/drm/card%d-DSI-1/status", card);
		if (!file_has_prefix(path, "connected"))
			continue;
		snprintf(path, sizeof(path),
			 "/sys/class/drm/card%d-DSI-1/enabled", card);
		if (file_has_prefix(path, "enabled"))
			return 1;
	}
	return 0;
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

static int read_logo_asset(uint8_t **pixels_out)
{
	static const uint8_t magic[8] = { 'R', 'D', 'K', 'L', 'O', 'G', 'O', '1' };
	struct logo_header header;
	struct stat status;
	uint8_t *pixels = NULL;
	int fd = -1;
	int result = -1;

	fd = open(LOGO_ASSET, O_RDONLY | O_CLOEXEC);
	if (fd < 0 || fstat(fd, &status) < 0 ||
	    read(fd, &header, sizeof(header)) != (ssize_t)sizeof(header))
		goto out;
	if (memcmp(header.magic, magic, sizeof(magic)) ||
	    header.header_bytes != sizeof(header) || header.format != 1 ||
	    header.width != CONSOLEOS_LOGO_WIDTH ||
	    header.height != CONSOLEOS_LOGO_HEIGHT ||
	    header.payload_bytes !=
		(size_t)header.width * header.height * 4U ||
	    status.st_size != (off_t)(sizeof(header) + header.payload_bytes))
		goto out;
	pixels = malloc(header.payload_bytes);
	if (!pixels || read(fd, pixels, header.payload_bytes) !=
		(ssize_t)header.payload_bytes)
		goto out;
	*pixels_out = pixels;
	pixels = NULL;
	result = 0;

out:
	free(pixels);
	if (fd >= 0)
		close(fd);
	return result;
}

static int draw_redika(struct framebuffer *fb, const uint8_t *logo,
		       struct activation_result *activation)
{
	struct fb_var_screeninfo requested;
	struct fb_var_screeninfo readback;
	unsigned int bytes_per_pixel = (fb->var.bits_per_pixel + 7U) / 8U;
	unsigned int x;
	unsigned int y;
	int unblank = FB_BLANK_UNBLANK;

	memset(activation, 0, sizeof(*activation));
	if (!usable_format(fb) ||
	    fb->var.xres != CONSOLEOS_NATIVE_WIDTH ||
	    fb->var.yres != CONSOLEOS_NATIVE_HEIGHT ||
	    CONSOLEOS_LOGO_X + CONSOLEOS_LOGO_WIDTH > fb->var.xres ||
	    CONSOLEOS_LOGO_Y + CONSOLEOS_LOGO_HEIGHT > fb->var.yres)
		return -1;

	requested = fb->var;
	requested.activate = FB_ACTIVATE_NOW | FB_ACTIVATE_FORCE;
	errno = 0;
	activation->put_rc = ioctl(fb->fd, FBIOPUT_VSCREENINFO, &requested);
	activation->put_errno = activation->put_rc < 0 ? errno : 0;

	memset(fb->mapping, 0, fb->mapping_size);
	for (y = 0; y < CONSOLEOS_LOGO_HEIGHT; y++) {
		uint8_t *row = fb->mapping +
			(size_t)(CONSOLEOS_LOGO_Y + y + fb->var.yoffset) *
				fb->fix.line_length +
			(size_t)(CONSOLEOS_LOGO_X + fb->var.xoffset) *
				bytes_per_pixel;
		for (x = 0; x < CONSOLEOS_LOGO_WIDTH; x++) {
			size_t input = ((size_t)y * CONSOLEOS_LOGO_WIDTH + x) * 4U;
			uint32_t value = pixel_value(logo[input + 2], logo[input + 1],
						   logo[input], &fb->var);
			store_pixel(row + (size_t)x * bytes_per_pixel, value,
				    bytes_per_pixel);
		}
	}
	errno = 0;
	activation->sync_rc = msync(fb->mapping, fb->mapping_size, MS_SYNC);
	activation->sync_errno = activation->sync_rc < 0 ? errno : 0;
	errno = 0;
	activation->pan_rc = ioctl(fb->fd, FBIOPAN_DISPLAY, &requested);
	activation->pan_errno = activation->pan_rc < 0 ? errno : 0;
	errno = 0;
	activation->unblank_rc = ioctl(fb->fd, FBIOBLANK, unblank);
	activation->unblank_errno = activation->unblank_rc < 0 ? errno : 0;
	errno = 0;
	activation->get_rc = ioctl(fb->fd, FBIOGET_VSCREENINFO, &readback);
	activation->get_errno = activation->get_rc < 0 ? errno : 0;
	if (!activation->get_rc)
		activation->mode_matches =
			readback.xres == requested.xres &&
			readback.yres == requested.yres &&
			readback.xoffset == requested.xoffset &&
			readback.yoffset == requested.yoffset &&
			readback.bits_per_pixel == requested.bits_per_pixel;
	activation->connector_enabled = dsi_connector_enabled();

	/*
	 * A memory copy or a successful write ioctl alone is not a visible-
	 * handoff proof.  Require the fbdev mode to read back unchanged and the
	 * MSM DSI connector to report that it is enabled after the commit.
	 */
	return activation->unblank_rc == 0 &&
	       (activation->put_rc == 0 || activation->pan_rc == 0) &&
	       activation->mode_matches && activation->connector_enabled ? 0 : -1;
}

static int handoff_firmware_frame(void)
{
	struct timespec delay = { .tv_nsec = POLL_NS };
	struct framebuffer source;
	struct framebuffer target;
	struct activation_result activation;
	uint8_t *rgb = NULL;
	uint8_t *logo = NULL;
	FILE *log;
	int attempt;
	int result = 1;

	log = fopen("/run/consoleos-early-display.log", "w");
	log_marker(log, "start", NULL);
	if (read_logo_asset(&logo)) {
		if (log) {
			fprintf(log, "logo-asset-invalid path=%s errno=%d\n",
				LOGO_ASSET, errno);
			fflush(log);
		}
		goto out;
	}
	if (log) {
		fprintf(log, "logo-asset-valid %ux%u at %u,%u\n",
			CONSOLEOS_LOGO_WIDTH, CONSOLEOS_LOGO_HEIGHT,
			CONSOLEOS_LOGO_X, CONSOLEOS_LOGO_Y);
		fflush(log);
	}
	for (attempt = 0; attempt < CAPTURE_ATTEMPTS; attempt++) {
		if (!open_framebuffer("/dev/fb0", &source)) {
			if (is_msm_framebuffer(&source)) {
				close_framebuffer(&source);
				break;
			}
			if (usable_format(&source) &&
			    !capture_rgb(&source, &rgb)) {
				log_marker(log, "captured-firmware", &source);
				log_capture_stats(log, rgb, source.var.xres,
						  source.var.yres);
				close_framebuffer(&source);
				break;
			}
			close_framebuffer(&source);
		}
		nanosleep(&delay, NULL);
	}
	if (!rgb && log) {
		fprintf(log,
			"firmware-capture-missing; continuing with generated asset\n");
		fflush(log);
	}

	for (attempt = 0; attempt < HANDOFF_ATTEMPTS; attempt++) {
		int index;

		for (index = 0; index < 4; index++) {
			char path[16];

			snprintf(path, sizeof(path), "/dev/fb%d", index);
			if (open_framebuffer(path, &target))
				continue;
			if (is_msm_framebuffer(&target)) {
				if (!draw_redika(&target, logo, &activation)) {
					log_activation(log, &activation);
					log_marker(log, "activated-msm", &target);
					result = 0;
				} else {
					log_activation(log, &activation);
					log_marker(log, "activation-failed-msm", &target);
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
	free(logo);
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
