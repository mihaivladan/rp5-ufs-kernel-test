// SPDX-License-Identifier: GPL-2.0-only
/*
 * Read-only diagnostic access to selected ADSP-owned Qualcomm SMEM items.
 *
 * This module deliberately has no write interface and never allocates or
 * modifies SMEM.  Each debugfs read performs three bounded copies so that
 * userspace can distinguish a stable snapshot from a record that changed
 * while it was being observed.
 */

#include <linux/debugfs.h>
#include <linux/delay.h>
#include <linux/err.h>
#include <linux/io.h>
#include <linux/kernel.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/seq_file.h>
#include <linux/slab.h>
#include <linux/soc/qcom/smem.h>

#define CONSOLEOS_SMEM_HOST_ADSP	2U
#define CONSOLEOS_SMEM_SAMPLE_COUNT	3U
#define CONSOLEOS_SMEM_SAMPLE_DELAY_US	10000U
#define CONSOLEOS_SMEM_MAX_SIZE	8192U
#define CONSOLEOS_SMEM_HEX_CHUNK	32U

struct consoleos_smem_item {
	u32 id;
	const char *name;
};

static const struct consoleos_smem_item consoleos_smem_items[] = {
	{ .id = 606, .name = "item606" },
	{ .id = 624, .name = "item624" },
	{ .id = 634, .name = "item634" },
};

static struct dentry *consoleos_smem_root;

static void consoleos_smem_print_sample(struct seq_file *m, unsigned int sample,
					const u8 *data, size_t size, u64 boottime_ns)
{
	size_t offset;

	seq_printf(m, "sample=%u boottime_ns=%llu\n", sample,
		   (unsigned long long)boottime_ns);
	for (offset = 0; offset < size; offset += CONSOLEOS_SMEM_HEX_CHUNK) {
		size_t length = min_t(size_t, CONSOLEOS_SMEM_HEX_CHUNK,
				      size - offset);

		seq_printf(m, "sample=%u offset=%04zx data=%*phN\n", sample,
			   offset, (int)length, data + offset);
	}
}

static int consoleos_smem_show(struct seq_file *m, void *unused)
{
	const struct consoleos_smem_item *item = m->private;
	void __iomem *smem;
	u8 *samples;
	u64 timestamps[CONSOLEOS_SMEM_SAMPLE_COUNT];
	size_t size = 0;
	unsigned int sample;
	bool stable = true;
	long error;

	seq_printf(m, "reader=consoleos_smem_reader_v1 host=%u item=%u\n",
		   CONSOLEOS_SMEM_HOST_ADSP, item->id);

	smem = (void __iomem *)qcom_smem_get(CONSOLEOS_SMEM_HOST_ADSP,
					       item->id, &size);
	if (IS_ERR(smem)) {
		error = PTR_ERR(smem);
		seq_printf(m, "status=error errno=%ld size=0\n", error);
		return 0;
	}

	seq_printf(m, "allocation_size=%zu max_size=%u sample_count=%u delay_us=%u\n",
		   size, CONSOLEOS_SMEM_MAX_SIZE, CONSOLEOS_SMEM_SAMPLE_COUNT,
		   CONSOLEOS_SMEM_SAMPLE_DELAY_US);
	if (!size) {
		seq_puts(m, "status=invalid reason=zero-size\n");
		return 0;
	}
	if (size > CONSOLEOS_SMEM_MAX_SIZE) {
		seq_puts(m, "status=rejected reason=oversize\n");
		return 0;
	}

	samples = kmalloc_array(CONSOLEOS_SMEM_SAMPLE_COUNT, size, GFP_KERNEL);
	if (!samples)
		return -ENOMEM;

	for (sample = 0; sample < CONSOLEOS_SMEM_SAMPLE_COUNT; sample++) {
		timestamps[sample] = ktime_get_boottime_ns();
		memcpy_fromio(samples + sample * size, smem, size);
		if (sample + 1 < CONSOLEOS_SMEM_SAMPLE_COUNT)
			usleep_range(CONSOLEOS_SMEM_SAMPLE_DELAY_US,
				     CONSOLEOS_SMEM_SAMPLE_DELAY_US + 1000);
	}

	for (sample = 1; sample < CONSOLEOS_SMEM_SAMPLE_COUNT; sample++) {
		if (memcmp(samples, samples + sample * size, size)) {
			stable = false;
			break;
		}
	}

	seq_printf(m, "status=ok stable=%u\n", stable);
	for (sample = 0; sample < CONSOLEOS_SMEM_SAMPLE_COUNT; sample++)
		consoleos_smem_print_sample(m, sample, samples + sample * size,
					    size, timestamps[sample]);

	kfree(samples);
	return 0;
}

static int consoleos_smem_open(struct inode *inode, struct file *file)
{
	return single_open(file, consoleos_smem_show, inode->i_private);
}

static const struct file_operations consoleos_smem_fops = {
	.owner = THIS_MODULE,
	.open = consoleos_smem_open,
	.read = seq_read,
	.llseek = seq_lseek,
	.release = single_release,
};

static int __init consoleos_smem_init(void)
{
	unsigned int index;

	consoleos_smem_root = debugfs_create_dir("consoleos_smem", NULL);
	if (IS_ERR(consoleos_smem_root))
		return PTR_ERR(consoleos_smem_root);
	if (!consoleos_smem_root)
		return -ENODEV;

	for (index = 0; index < ARRAY_SIZE(consoleos_smem_items); index++) {
		struct dentry *entry;

		entry = debugfs_create_file(consoleos_smem_items[index].name, 0400,
					    consoleos_smem_root,
					    (void *)&consoleos_smem_items[index],
					    &consoleos_smem_fops);
		if (IS_ERR_OR_NULL(entry)) {
			debugfs_remove_recursive(consoleos_smem_root);
			return entry ? PTR_ERR(entry) : -ENOMEM;
		}
	}

	pr_info("consoleos_smem_reader: read-only ADSP SMEM items 606/624/634\n");
	return 0;
}

static void __exit consoleos_smem_exit(void)
{
	debugfs_remove_recursive(consoleos_smem_root);
}

module_init(consoleos_smem_init);
module_exit(consoleos_smem_exit);

MODULE_AUTHOR("ConsoleOS project");
MODULE_DESCRIPTION("Read-only bounded Qualcomm ADSP SMEM diagnostic reader");
MODULE_LICENSE("GPL");
