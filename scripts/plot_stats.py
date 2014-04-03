#!/usr/bin/env python
# vim: fileencoding=utf-8

from argparse import ArgumentParser
from os.path import expanduser
from collections import defaultdict
import glob
import math
import os
import sqlite3

import matplotlib
import matplotlib.pyplot as plt
import numpy as np

matplotlib.rc('axes', labelsize='large')
matplotlib.rc('figure', figsize=(8, 1.5))
matplotlib.rc('figure.subplot', left=0.09, right=0.92, bottom=0.275)
matplotlib.rc('font', size=7)
matplotlib.rc('font', **{'sans-serif' : 'DejaVu LGC Sans', 'family' : 'sans-serif'})

class SmartList(list):
    """Stupid extended list."""
    def __init__(self, *args, **kwargs):
        list.__init__(self, *args, **kwargs)
    def __add__(self, other):
        return super(SmartList, self).__add__([other])
    def __iadd__(self, other):
        return super(SmartList, self).__iadd__([other])

def cumulative_sum(list, total=0):
    for item in list:
        total += item
        yield total

def html_tag(tag, *args, **kwargs):
    attr = " ".join(['{0}="{1}"'.format(a, b.replace('"', r'\"')) for a, b in kwargs.items()])
    return '<{0}>\n{1}\n</{2}>\n'.format(" ".join([tag, attr]), "\n".join(args), tag)

def html_table(headers, rows, **kwargs):
    import itertools
    row_classes = itertools.cycle(["tr class=alt", "tr"])

    top = [html_tag('tr', *(html_tag('th', h) for h in headers))]
    body = [html_tag(rc, *(html_tag('td', x) for x in row)) for rc, row in itertools.izip(row_classes, rows)]

    return html_tag('table', *(top+body), **kwargs)

def make_histo(a, num_bins, xlabel, ylabel, filename, dir, **kwargs):
    # fig, (ax0, ax1) = plt.subplots(nrows=2, sharex=True)
    if 'log' in kwargs:
        if kwargs['log'] == True or kwargs['log'] == 'y':
            plt.yscale('log')
        elif kwargs['log'] == 'x':
            plt.xscale('log')
        del kwargs['log']

    if 'stats' in kwargs:
        stats = kwargs['stats']
        del kwargs['stats']
    else:
        stats = False

    if 'histtype' in kwargs:
        plt.hist(a, bins=num_bins, **kwargs)
    else:
        plt.hist(a, bins=num_bins, histtype='barstacked', **kwargs)

    plt.grid(True)

    if stats:
        all = np.concatenate(a)
        avg = np.average(all)
        var = np.std(all)
        med = np.median(all)
        plt.figtext(0.75, 0.775, u"μ = {0:.3g}, σ = {1:.3g}".format(avg, var), ha="center")
        plt.figtext(0.75, 0.7, u"median = {0:.3g}".format(med), ha="center")

    plt.xlabel(xlabel)
    plt.ylabel(ylabel)

    try:
        plt.axis(xmax=num_bins[-1])
    except:
        pass

    if 'label' in kwargs:
        plt.legend(bbox_to_anchor=(0.5, 0.9), loc='lower center', ncol=len(kwargs['label']), prop={'size': 7})

    return save_and_close(dir, filename)

def make_frequency_pie(a, name, dir, threshold=0.05):
    vals = np.unique(a)
    counts = [len(a[a == v]) for v in vals]
    total = sum(counts)

    counts, vals = zip(*filter(lambda (c, l): c / float(total) >= threshold, zip(counts, vals)))
    rest = total - sum(counts)

    plt.pie(counts + (rest, ), labels=vals + ('Other', ))
    fig = plt.gcf()
    fig.set_size_inches(3, 3)
    fig.subplots_adjust(left=0.05, bottom=0.05, right=0.95, top=0.95)

    return save_and_close(dir, name)

def make_plot(tuples, x_label, y_label, name, dir, fun=matplotlib.axes.Axes.plot, y_label2=None, **kwargs):
    fig, ax1 = plt.subplots()

    plots1 = tuples[0] if y_label2 else tuples

    for x, y, l in plots1:
        fun(ax1, x, y, label=l)
        ax1.axis(xmax=x[-1], ymax=y[-1])
    ax1.set_xlabel(x_label)
    ax1.set_ylabel(y_label)
    ax1.legend(loc='upper left')
    ax1.grid(True)

    if y_label2:
        ax2 = ax1.twinx()
        for x, y, l in tuples[1]:
            fun(ax2, x, y, ':', label=l)
            ax2.axis(xmax=x[-1])
        ax2.set_ylabel(y_label2)
        ax2.legend(loc='upper right')

    if 'log' in kwargs:
        if kwargs['log'] == True or kwargs['log'] == 'y':
            plt.yscale('log')
        elif kwargs['log'] == 'x':
            plt.xscale('log')
        del kwargs['log']
    # plt.legend()
    num = len(tuples[0]) + len(tuples[1]) if y_label2 else len(tuples)

    return save_and_close(dir, name)

def make_profile(x, y, bins, xlabel, ylabel, name, dir, yrange=None):
    sums, edges = np.histogram(x, bins=bins, weights=y)
    squares, edges = np.histogram(x, bins=bins, weights=np.multiply(y, y))
    counts, edges = np.histogram(x, bins=bins)
    avg = np.divide(sums, counts)
    avg_sq = np.divide(squares, counts)
    err = np.sqrt(np.subtract(avg_sq, np.multiply(avg, avg)))

    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    centers = [(x + y) / 2.0 for x, y in zip(edges[:-1], edges[1:])]
    plt.errorbar(centers, avg, yerr=err, fmt='o', ms=3, capsize=0)
    plt.axis(xmax=bins[-1], ymin=0)
    plt.grid(True)

    return save_and_close(dir, name)

def make_scatter(x, y, bins, xlabel, ylabel, name, dir, yrange=None):
    plt.hexbin(x, y, cmap=plt.cm.Purples, gridsize=(len(bins) - 1, 10))
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    if yrange is not None:
        plt.ylim(yrange)
    plt.axis(xmax=bins[-1])

    return save_and_close(dir, name)

def read_debug(workdir):
    lobster_create = []
    lobster_return = []
    sqlite_create = []
    sqlite_return = []

    lob_file = os.path.join(workdir, 'debug_lobster_times')
    sql_file = os.path.join(workdir, 'debug_sql_times')
    if os.path.exists(lob_file):
        with open(lob_file) as f:
            for l in f:
                items = l.split()
                if l.startswith("CREA"):
                    lobster_create += [float(items[-1])] * int(items[1])
                else:
                    lobster_return += [float(items[-1])] * int(items[1])
    if os.path.exists(sql_file):
        with open(sql_file) as f:
            for l in f:
                items = l.split()
                if l.startswith("CREA"):
                    sqlite_create += [float(items[-1])] * int(items[1])
                else:
                    sqlite_return += [float(items[-1])] * int(items[1])
    return (
            np.asarray(lobster_create),
            np.asarray(lobster_return),
            np.asarray(sqlite_create),
            np.asarray(sqlite_return)
            )

def reduce(a, idx, interval):
    quant = a[:,idx]
    last = quant[0]
    select = np.ones((len(quant),), dtype=np.bool)
    for i in range(1, len(quant) - 1):
        if quant[i] - last > interval or quant[i + 1] - last > interval:
            select[i] = True
            last = quant[i]
        else:
            select[i] = False

    return a[select]

def split_by_column(a, col, key=lambda x: x, threshold=None):
    """Split an array into multiple ones, based on unique values in the named
    column `col`.
    """
    keys = np.unique(a[col])
    vals = [a[a[col] == v] for v in keys]
    keys = map(key, keys)

    if threshold:
        total = float(len(a))
        others = filter(lambda v: len(v) / total < threshold, vals)
        keys, vals = zip(*filter(lambda (k, v): len(v) / total >= threshold, zip(keys, vals)))
        if len(others) > 0:
            keys += ("Other", )
            vals += (np.concatenate(others), )

    return keys, vals

def save_and_close(dir, name):
    if not os.path.exists(dir):
        os.makedirs(dir)
    print "Saving", name
    # plt.gcf().set_size_inches(6, 1.5)

    plt.savefig(os.path.join(dir, '%s.png' % name))
    # plt.savefig(os.path.join(dir, '%s.pdf' % name))

    plt.close()

    return html_tag("img", src='{0}.png'.format(name))

if __name__ == '__main__':
    parser = ArgumentParser(description='make histos')
    parser.add_argument('directory', help="Specify input directory")
    parser.add_argument('outdir', nargs='?', help="Specify output directory")
    parser.add_argument("--xmin", type=int, help="Specify custom x-axis minimum", default=0, metavar="MIN")
    parser.add_argument("--xmax", type=int, help="Specify custom x-axis maximum", default=None, metavar="MAX")
    parser.add_argument('--samplelogs', action='store_true', help='Make a table with links to sample error logs', default=False)
    args = parser.parse_args()

    if args.outdir:
        top_dir = args.outdir
    else:
        top_dir = os.path.join(os.environ['HOME'], 'www',  os.path.basename(os.path.normpath(args.directory)))

    print 'Saving plots to: ' + top_dir
    if not os.path.isdir(top_dir):
        os.makedirs(top_dir)

    jtags = SmartList()
    dtags = SmartList()
    wtags = SmartList()

    print "Reading WQ log"
    with open(os.path.join(args.directory, 'work_queue.log')) as f:
        headers = dict(map(lambda (a, b): (b, a), enumerate(f.readline()[1:].split())))
    wq_stats_raw_all = np.loadtxt(os.path.join(args.directory, 'work_queue.log'))
    start_time = wq_stats_raw_all[0,0]
    end_time = wq_stats_raw_all[-1,0]

    if not args.xmax:
        xmax = end_time
    else:
        xmax = args.xmax * 60e6 + start_time

    xmin = args.xmin * 60e6 + start_time

    wq_stats_raw = wq_stats_raw_all[np.logical_and(wq_stats_raw_all[:,0] >= xmin, wq_stats_raw_all[:,0] <= xmax),:]

    orig_times = wq_stats_raw[:,0].copy()
    # subtract start time, convert to minutes
    wq_stats_raw[:,0] = (wq_stats_raw[:,0] - start_time) / 60e6
    runtimes = wq_stats_raw[:,0]
    print "First iteration..."
    print "start_time = ",int(start_time)

    bins = xrange(args.xmin, int(runtimes[-1]) + 5, 5)
    scale = int(max(len(bins) / 100.0, 1.0))
    bins = xrange(args.xmin, int(runtimes[-1]) + scale * 5, scale * 5)
    wtags += make_histo([runtimes], bins, 'Time (m)', 'Activity', 'activity', top_dir, log=True)

    transferred = (wq_stats_raw[:,headers['total_bytes_received']] - np.roll(wq_stats_raw[:,headers['total_bytes_received']], 1, 0)) / 1024**3
    transferred[transferred < 0] = 0

    bins = xrange(args.xmin, int(runtimes[-1]) + 60, 60)
    wtags += make_histo([runtimes], bins, 'Time (m)', 'Output (GB/h)', 'rate', top_dir, weights=[transferred])

    print "Reducing WQ log"
    wq_stats = reduce(wq_stats_raw, 0, 5.)
    runtimes = wq_stats[:,0]

    wtags += make_plot(([(runtimes, wq_stats[:,headers['workers_busy']], 'busy'),
               (runtimes, wq_stats[:,headers['workers_idle']], 'idle'),
               (runtimes, wq_stats[:,headers['total_workers_connected']], 'connected')],
               [(runtimes, wq_stats[:,headers['tasks_running']], 'running')]),
               'Time (m)', 'Workers' , 'workers_active', top_dir, y_label2='Tasks')

    db = sqlite3.connect(os.path.join(args.directory, 'lobster.db'))
    stats = {}

    failed_jobs = np.array(db.execute("""select
        id,
        host,
        dataset,
        exit_code,
        time_submit,
        time_retrieved,
        time_total_on_worker
        from jobs
        where status=3 and time_retrieved>=? and time_retrieved<=?""",
        (xmin / 1e6, xmax / 1e6)).fetchall(),
            dtype=[
                ('id', 'i4'),
                ('host', 'a50'),
                ('dataset', 'i4'),
                ('exit_code', 'i4'),
                ('t_submit', 'i4'),
                ('t_retrieved', 'i4'),
                ('t_allput', 'i8')
                ])

    success_jobs = np.array(db.execute("""select * from jobs
        where (status=2 or status=5 or status=6) and time_retrieved>=? and time_retrieved<=?""",
        (xmin / 1e6, xmax / 1e6)).fetchall(),
            dtype=[
                ('id', 'i4'),
                ('host', 'a50'),
                ('dataset', 'i4'),
                ('file_block', 'a100'),
                ('status', 'i4'),
                ('exit_code', 'i4'),
                ('retries', 'i4'),
                ('missed_lumis', 'i4'),
                ('t_submit', 'i4'),
                ('t_send_start', 'i4'),
                ('t_send_end', 'i4'),
                ('t_wrapper_start', 'i4'),
                ('t_wrapper_ready', 'i4'),
                ('t_file_req', 'i4'),
                ('t_file_open', 'i4'),
                ('t_first_ev', 'i4'),
                ('t_wrapper_end', 'i4'),
                ('t_recv_start', 'i4'),
                ('t_recv_end', 'i4'),
                ('t_retrieved', 'i4'),
                ('t_goodput', 'i8'),
                ('t_allput', 'i8'),
                ('b_recv', 'i4'),
                ('b_sent', 'i4')
                ])

    total_time_failed = np.sum(failed_jobs['t_allput'])
    total_time_success = np.sum(success_jobs['t_allput'])
    total_time_good = np.sum(success_jobs['t_goodput'])
    total_time_pure = np.sum(success_jobs['t_wrapper_end'] - success_jobs['t_first_ev']) * 1e6


    bins = xrange(args.xmin, int(runtimes[-1]) + 5, 5)
    scale = int(max(len(bins) / 100.0, 1.0))
    bins = xrange(args.xmin, int(runtimes[-1]) + scale * 5, scale * 5)
    success_times = (success_jobs['t_retrieved'] - start_time / 1e6) / 60
    failed_times = (failed_jobs['t_retrieved'] - start_time / 1e6) / 60

    wtags += make_histo([success_times, failed_times], bins, 'Time (m)', 'Jobs', 'jobs', top_dir, label=['succesful', 'failed'], color=['green', 'red'])
    wtags += make_profile(
            (success_jobs['t_wrapper_start'] - start_time / 1e6) / 60,
            (success_jobs['t_first_ev'] - success_jobs['t_wrapper_start']) / 60.,
            bins, 'Wrapper start time (m)', 'Overhead (m)', 'overhead_vs_time', top_dir)

    fail_labels, fail_values = split_by_column(failed_jobs, 'exit_code', threshold=0.025)
    fail_times = [(vs['t_retrieved'] - start_time / 1e6) / 60 for vs in fail_values]
    wtags += make_histo(fail_times, bins, 'Time (m)', 'Jobs',
            'fail_times', top_dir, label=map(str, fail_labels))

    wtags += make_scatter(
            (failed_jobs['t_retrieved'] - start_time / 1e6) / 60,
            failed_jobs['exit_code'],
            bins, 'Time (m)', 'Exit Code', 'exit_code_vs_time', top_dir,
            [min(failed_jobs['exit_code']) - 5, max(failed_jobs['exit_code']) + 5])

    #for cases where jobits per job changes during run, get per-jobit info
    success_jobits = np.array(db.execute("""select jobits.id, jobs.time_retrieved
        from jobits, jobs where jobits.job==jobs.id and
        (jobits.status=2 or jobits.status=5 or jobits.status=6) and
        jobs.time_retrieved>=? and jobs.time_retrieved<=?
        group by jobs.time_retrieved""",
        (xmin / 1e6, xmax / 1e6)).fetchall(),
            dtype=[('id', 'i4'), ('t_retrieved', 'i4')])
    total_jobits = db.execute('select count(*) from jobits').fetchone()[0]

    finished_jobit_times = (success_jobits['t_retrieved'] - start_time / 1e6) / 60
    finished_jobit_hist, jobit_bins = np.histogram(finished_jobit_times)
    bin_centers = [(x+y)/2 for x, y in zip(jobit_bins[:-1], jobit_bins[1:])]

    wtags += make_plot([(bin_centers, list(cumulative_sum(finished_jobit_hist, 0)), 'total finished'),
                        (bin_centers, list(cumulative_sum([-x for x in finished_jobit_hist], total_jobits)), 'total unfinished')],
                       'Time (m)', 'Jobits' , 'finished_jobits', top_dir, log=True)

    label2id = {}
    id2label = {}

    for dset_label, dset_id in db.execute('select label, id from datasets'):
        label2id[dset_label] = dset_id
        id2label[dset_id] = dset_label

    dset_labels, dset_values = split_by_column(success_jobs, 'dataset', key=lambda x: id2label[x])

    num_bins = 30
    total_times = [(vs['t_wrapper_end'] - vs['t_wrapper_start']) / 60. for vs in dset_values]
    processing_times = [(vs['t_wrapper_end'] - vs['t_first_ev']) / 60. for vs in dset_values]
    overhead_times = [(vs['t_first_ev'] - vs['t_wrapper_start']) / 60. for vs in dset_values]
    idle_times = [(vs['t_wrapper_start'] - vs['t_send_end']) / 60. for vs in dset_values]
    init_times = [(vs['t_wrapper_ready'] - vs['t_wrapper_start']) / 60. for vs in dset_values]
    cmsrun_times = [(vs['t_first_ev'] - vs['t_wrapper_ready']) / 60. for vs in dset_values]

    stageout_times = [(vs['t_retrieved'] - vs['t_wrapper_end']) / 60. for vs in dset_values]
    wait_times = [(vs['t_recv_start'] - vs['t_wrapper_end']) / 60. for vs in dset_values]
    transfer_times = [(vs['t_recv_end'] - vs['t_recv_start']) / 60. for vs in dset_values]
    transfer_bytes = [vs['b_recv'] / 1024.0**2 for vs in dset_values]
    transfer_rates = []
    for (bytes, times) in zip(transfer_bytes, transfer_times):
        transfer_rates.append(np.divide(bytes[times != 0], times[times != 0] * 60.))

    send_times = [(vs['t_send_end'] - vs['t_send_start']) / 60. for vs in dset_values]
    send_bytes = [vs['b_sent'] / 1024.0**2 for vs in dset_values]
    send_rates = []
    for (bytes, times) in zip(send_bytes, send_times):
        send_rates.append(np.divide(bytes[times != 0], times[times != 0] * 60.))
    put_ratio = [np.divide(vs['t_goodput'] * 1.0, vs['t_allput']) for vs in dset_values]
    pureput_ratio = [np.divide((vs['t_wrapper_end'] -  vs['t_first_ev']) * 1e6, vs['t_allput']) for vs in dset_values]


    (l_cre, l_ret, s_cre, s_ret) = read_debug(args.directory)

    jtags += make_histo(total_times, num_bins, 'Runtime (m)', 'Jobs', 'run_time', top_dir, label=dset_labels, stats=True)
    jtags += make_histo(processing_times, num_bins, 'Pure processing time (m)', 'Jobs', 'processing_time', top_dir, label=dset_labels, stats=True)
    jtags += make_histo(overhead_times, num_bins, 'Overhead time (m)', 'Jobs', 'overhead_time', top_dir, label=dset_labels, stats=True)
    jtags += make_histo(idle_times, num_bins, 'Idle time (m) - End receive job data to wrapper start', 'Jobs', 'idle_time', top_dir, label=dset_labels, stats=True)
    jtags += make_histo(init_times, num_bins, 'Wrapper initialization time (m)', 'Jobs', 'wrapper_time', top_dir, label=dset_labels, stats=True)
    jtags += make_histo(cmsrun_times, num_bins, 'cmsRun startup time (m)', 'Jobs', 'cmsrun_time', top_dir, label=dset_labels, stats=True)
    jtags += make_histo(stageout_times, num_bins, 'Stage-out time (m)', 'Jobs', 'stageout_time', top_dir, label=dset_labels, stats=True)
    jtags += make_histo(wait_times, num_bins, 'Wait time (m)', 'Jobs', 'wait_time', top_dir, label=dset_labels, stats=True)
    jtags += make_histo(transfer_times, num_bins, 'Transfer time (m)', 'Jobs', 'transfer_time', top_dir, label=dset_labels, stats=True)
    jtags += make_histo(transfer_bytes,
            num_bins, 'Data received (MiB)', 'Jobs', 'recv_data', top_dir,
            label=dset_labels, stats=True)
    jtags += make_histo(transfer_rates,
            num_bins, 'Data received rate (MiB/s)', 'Jobs', 'recv_rate', top_dir,
            label=dset_labels, stats=True)

    if args.samplelogs:
        jtags += html_tag('a', make_frequency_pie(failed_jobs['exit_code'], 'exit_codes', top_dir), href='errors.html')
    else:
        jtags += make_frequency_pie(failed_jobs['exit_code'], 'exit_codes', top_dir)

    dtags += make_histo(send_times, num_bins, 'Send time (m)', 'Jobs', 'send_time', top_dir, label=dset_labels, stats=True)
    dtags += make_histo(send_bytes,
            num_bins, 'Data sent (MiB)', 'Jobs', 'send_data', top_dir,
            label=dset_labels, stats=True)
    dtags += make_histo(send_rates,
            num_bins, 'Data sent rate (MiB/s)', 'Jobs', 'send_rate', top_dir,
            label=dset_labels, stats=True)
    # dtags += make_histo(put_ratio, num_bins, 'Goodput / (Goodput + Badput)', 'Jobs', 'put_ratio', top_dir, label=[vs[0] for vs in dset_values], stats=True)
    dtags += make_histo(put_ratio, [0.05 * i for i in range(21)], 'Goodput / (Goodput + Badput)', 'Jobs', 'put_ratio', top_dir, label=dset_labels, stats=True)
    dtags += make_histo(pureput_ratio, [0.05 * i for i in range(21)], 'Pureput / (Goodput + Badput)', 'Jobs', 'pureput_ratio', top_dir, label=dset_labels, stats=True)

    log_bins = [10**(-4 + 0.25 * n) for n in range(21)]
    dtags += make_histo([s_cre[s_cre > 0]], log_bins, 'Job creation SQL query time (s)', 'Jobs', 'create_sqlite_time', top_dir, stats=True, log='x')
    dtags += make_histo([(l_cre - s_cre)[s_cre > 0]], log_bins, 'Job creation lobster overhead time (s)', 'Jobs', 'create_lobster_time', top_dir, stats=True, log='x')
    dtags += make_histo([s_ret], log_bins, 'Job return SQL query time (s)', 'Jobs', 'return_sqlite_time', top_dir, stats=True, log='x')
    dtags += make_histo([l_ret - s_ret], log_bins, 'Job return lobster overhead time (s)', 'Jobs', 'return_lobster_time', top_dir, stats=True, log='x')

    # hosts = vals['host']
    # host_clusters = np.char.rstrip(np.char.replace(vals['host'], '.crc.nd.edu', ''), '0123456789-')

    with open(os.path.join(top_dir, 'index.html'), 'w') as f:
        body = html_tag("div",
                *([html_tag("h2", "Job Statistics")] +
                    map(lambda t: html_tag("div", t, style="clear: both;"), jtags) +
                    [html_tag("h2", "Debug Job Statistics")] +
                    map(lambda t: html_tag("div", t, style="clear: both;"), dtags) +
                    [
                        html_tag("h2", "Lobster Statistics"),
                        html_tag("p", "Successful jobs: Goodput / Allput = {0:.3f}".format(total_time_good / float(total_time_success))),
                        html_tag("p", "Successful jobs: Pureput / Allput = {0:.3f}".format(total_time_pure / float(total_time_success))),
                        html_tag("p", "All jobs: Goodput / Allput = {0:.3f}".format(total_time_good / float(total_time_success + total_time_failed))),
                        html_tag("p", "All jobs: Pureput / Allput = {0:.3f}".format(total_time_pure / float(total_time_success + total_time_failed)))
                    ] +
                    map(lambda t: html_tag("div", t, style="clear: both;"), wtags)),
                style="margin: 1em auto; display: block; width: auto; text-align: center;")
        f.write(body)

    if args.samplelogs:
        with open(os.path.join(top_dir, 'errors.html'), 'w') as f:
            f.write("""
            <style>
            #errors
            {font-family:"Trebuchet MS", Arial, Helvetica, sans-serif;
            width:100%;
            border-collapse:collapse;}

            #errors td, #errors th
            {font-size:1em;
            border:1px solid #98bf21;
            padding:3px 7px 2px 7px;}

            #errors th
            {font-size:1.1em;
            text-align:left;
            padding-top:5px;
            padding-bottom:4px;
            background-color:#A7C942;
            color:#ffffff;}

            #errors tr.alt td
            {color:#000000;
            background-color:#EAF2D3;}
            </style>""")

            if not os.path.exists(os.path.join(top_dir, 'errors')):
                os.makedirs(os.path.join(top_dir, 'errors'))

            import shutil
            headers = []
            rows = [[], [], [], [], []]
            num_samples = 5
            for exit_code, jobs in zip(split_by_column(failed_jobs[['id', 'dataset', 'exit_code']], 'exit_code')):
                headers.append('Exit %i <br>(%i failed jobs)' % (exit_code, len(jobs)))
                print 'Copying sample logs for exit code ', exit_code
                for row, j in enumerate(list(jobs[:num_samples])+[()]*(num_samples-len(jobs))):
                    if len(j) == 0:
                        rows[row].append('')
                    else:
                        id, ds, e = j
                        from_path = os.path.join(args.directory, id2label[ds], 'failed', str(id))
                        to_path = os.path.join(os.path.join(top_dir, 'errors'), str(id))
                        if os.path.exists(to_path):
                            shutil.rmtree(to_path)
                        os.makedirs(to_path)
                        cell = []
                        for l in ['cmssw.log.gz', 'job.log.gz']:
                            if os.path.exists(os.path.join(from_path, l)):
                                shutil.copy(os.path.join(from_path, l), os.path.join(to_path, l))
                                os.popen('gunzip %s' % os.path.join(to_path, l)) #I don't know how to make our server serve these correctly
                                cell.append(html_tag('a', l.replace('.gz', ''), href=os.path.join('errors', str(id), l.replace('.gz', ''))))
                        rows[row].append(', '.join(cell))

            f.write(html_table(headers, rows, id='errors'))

