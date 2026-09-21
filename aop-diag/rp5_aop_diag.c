// SPDX-License-Identifier: GPL-2.0-only
/* RP5 AOP.HO.2.0-00194: firmware DDR logging, not direct ARC MMIO.
 * Requires both AOP partition hashes to be checked by the host before loading.
 * No arbitrary QMP, resource votes, ARC access, or automatic suspend.
 */
#include <linux/debugfs.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/seq_file.h>
#include <linux/soc/qcom/qcom_aoss.h>
#include <linux/uaccess.h>
#include <linux/workqueue.h>
#include <linux/ktime.h>
#include <clocksource/arm_arch_timer.h>

#define FW_HASH "1e673233ef5ca65778db6effe5f8e57eef880d657ccc047ad44c49f0f302cc14"
#define WORDS 256
static char *firmware_sha256;
module_param(firmware_sha256, charp, 0400);
MODULE_PARM_DESC(firmware_sha256, "Host-verified SHA256 of BOTH AOP partitions");
static struct qmp *qmp;
static struct dentry *root;
static struct platform_device *diag_device;
static void __iomem *ddr_ring, *aop_ring;
static DEFINE_MUTEX(control_lock);
static bool logging_dirty;
static int last_qmp_result;

struct snapshot {
	u64 ticks_before, ticks_after;
	u32 ddr[WORDS], aop[WORDS];
	bool valid;
};
static struct snapshot entry, resume;

static void capture(struct snapshot *s)
{
	unsigned int i;
	s->ticks_before = arch_timer_read_counter();
	/* Message RAM only permits scalar 32-bit accesses. */
	for (i = 0; i < WORDS; i++) {
		s->ddr[i] = readl(ddr_ring + 4 * i);
		s->aop[i] = readl(aop_ring + 4 * i);
	}
	s->ticks_after = arch_timer_read_counter();
	s->valid = true;
}

static int stop_logging(void)
{
	int ret = 0;
	if (logging_dirty) {
		ret = qmp_send(qmp, "{class: ddr, res: starc_log, val: 0}");
		last_qmp_result = ret;
		if (!ret)
			logging_dirty = false;
	}
	return ret;
}

static void timeout_logging(struct work_struct *work);
static DECLARE_DELAYED_WORK(logging_timeout, timeout_logging);
static void timeout_logging(struct work_struct *work)
{
	int ret;
	mutex_lock(&control_lock);
	ret = stop_logging();
	mutex_unlock(&control_lock);
	if (ret) {
		pr_err("rp5_aop_diag: logging rollback failed: %d\n", ret);
		queue_delayed_work(system_freezable_wq, &logging_timeout, 5 * HZ);
	}
}

static ssize_t control_write(struct file *f, const char __user *buf,
			     size_t count, loff_t *pos)
{
	char command[16];
	int ret = 0;
	if (!count || count >= sizeof(command))
		return -EINVAL;
	if (copy_from_user(command, buf, count))
		return -EFAULT;
	command[count] = '\0';
	mutex_lock(&control_lock);
	if (sysfs_streq(command, "starc_on")) {
		/* Even a timeout may mean AOP consumed it: always arrange rollback. */
		logging_dirty = true;
		ret = qmp_send(qmp, "{class: ddr, res: starc_log, val: 1}");
		last_qmp_result = ret;
		mod_delayed_work(system_freezable_wq, &logging_timeout, 300 * HZ);
	} else if (sysfs_streq(command, "starc_off")) {
		ret = stop_logging();
	} else {
		ret = -EINVAL;
	}
	mutex_unlock(&control_lock);
	return ret ? ret : count;
}
static const struct file_operations control_ops = {
	.owner = THIS_MODULE, .open = simple_open, .write = control_write,
};

static void print_snapshot(struct seq_file *m, const char *name,
			   const struct snapshot *s)
{
	unsigned int i;
	seq_printf(m, "snapshot %s valid %u ticks %llu %llu\n", name,
		   s->valid, s->ticks_before, s->ticks_after);
	if (!s->valid)
		return;
	for (i = 0; i < WORDS; i += 4)
		seq_printf(m, "ddr %02u %08x %08x %08x %08x\n", i / 4,
		 s->ddr[i], s->ddr[i+1], s->ddr[i+2], s->ddr[i+3]);
	for (i = 0; i < WORDS; i += 4)
		seq_printf(m, "aop %02u %08x %08x %08x %08x\n", i / 4,
		 s->aop[i], s->aop[i+1], s->aop[i+2], s->aop[i+3]);
}
static int snapshot_show(struct seq_file *m, void *unused)
{
	struct snapshot *now = kzalloc(sizeof(*now), GFP_KERNEL);
	if (!now)
		return -ENOMEM;
	mutex_lock(&control_lock);
	capture(now);
	seq_printf(m, "schema 1 firmware %s logging_dirty %u qmp_result %d\n",
		   FW_HASH, logging_dirty, last_qmp_result);
	print_snapshot(m, "suspend_noirq", &entry);
	print_snapshot(m, "resume_noirq", &resume);
	print_snapshot(m, "awake", now);
	mutex_unlock(&control_lock);
	kfree(now);
	return 0;
}
DEFINE_SHOW_ATTRIBUTE(snapshot);

static int diag_suspend_noirq(struct device *dev)
{
	resume.valid = false;
	capture(&entry);
	return 0;
}
static int diag_resume_noirq(struct device *dev)
{
	capture(&resume);
	return 0;
}
static const struct dev_pm_ops diag_pm = {
	.suspend_noirq = diag_suspend_noirq, .resume_noirq = diag_resume_noirq,
};
static int diag_probe(struct platform_device *pdev) { return 0; }
static struct platform_driver diag_driver = {
	.probe = diag_probe,
	.driver = { .name = "rp5-aop-diag", .pm = &diag_pm },
};

static int __init diag_init(void)
{
	struct device_node *np;
	struct device client = {};
	const char *model;
	int ret;
	if (!firmware_sha256 || strcmp(firmware_sha256, FW_HASH))
		return -EINVAL;
	np = of_find_node_by_path("/");
	ret = of_property_read_string(np, "model", &model);
	if (!ret && strcmp(model, "Retroid Pocket 5"))
		ret = -ENODEV;
	of_node_put(np);
	if (ret)
		return ret;
	qmp = ERR_PTR(-ENODEV);
	/* qmp_get consumes only dev->of_node, and holds its provider reference. */
	for_each_node_with_property(np, "qcom,qmp") {
		struct device_node *provider = of_parse_phandle(np, "qcom,qmp", 0);
		bool matches = of_device_is_compatible(provider, "qcom,sm8250-aoss-qmp");
		of_node_put(provider);
		if (!matches)
			continue;
		client.of_node = np;
		qmp = qmp_get(&client);
		if (!IS_ERR(qmp)) {
			of_node_put(np);
			break;
		}
	}
	if (IS_ERR(qmp))
		return PTR_ERR(qmp);
	ddr_ring = ioremap(0x0c360000, 1024);
	aop_ring = ioremap(0x0c370000, 1024);
	if (!ddr_ring || !aop_ring) {
		ret = -ENOMEM;
		goto unmap;
	}
	ret = platform_driver_register(&diag_driver);
	if (ret)
		goto unmap;
	diag_device = platform_device_register_simple("rp5-aop-diag", -1, NULL, 0);
	if (IS_ERR(diag_device)) {
		ret = PTR_ERR(diag_device);
		goto driver;
	}
	root = debugfs_create_dir("rp5_aop_diag", NULL);
	if (IS_ERR(root)) {
		ret = PTR_ERR(root);
		goto device;
	}
	debugfs_create_file("snapshots", 0400, root, NULL, &snapshot_fops);
	debugfs_create_file("control", 0200, root, NULL, &control_ops);
	pr_info("rp5_aop_diag: DDR/AOP message-RAM capture ready; logging unchanged\n");
	return 0;
device:
	platform_device_unregister(diag_device);
driver:
	platform_driver_unregister(&diag_driver);
unmap:
	if (ddr_ring) iounmap(ddr_ring);
	if (aop_ring) iounmap(aop_ring);
	qmp_put(qmp);
	return ret;
}
static void __exit diag_exit(void)
{
	debugfs_remove_recursive(root);
	cancel_delayed_work_sync(&logging_timeout);
	if (stop_logging())
		pr_err("rp5_aop_diag: AOP logging state uncertain; reboot to restore defaults\n");
	platform_device_unregister(diag_device);
	platform_driver_unregister(&diag_driver);
	iounmap(ddr_ring);
	iounmap(aop_ring);
	qmp_put(qmp);
}
module_init(diag_init);
module_exit(diag_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("RP5 firmware DDR log and suspend boundary capture");
