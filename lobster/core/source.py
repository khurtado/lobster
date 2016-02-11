import datetime
import glob
import json
import logging
import math
import os
import re
import shutil
import subprocess
import work_queue as wq
import yaml

from collections import defaultdict
from hashlib import sha1

from lobster import fs, se, util
from lobster.cmssw import dash
from lobster.core import unit
from lobster.core import MergeTaskHandler
from lobster.core import Workflow

logger = logging.getLogger('lobster.source')

class ReleaseSummary(object):
    """Summary of returned tasks.

    Prints a user-friendly summary of which tasks returned with what exit code/status.
    """

    flags = {
            wq.WORK_QUEUE_RESULT_INPUT_MISSING: "missing input",                # 1
            wq.WORK_QUEUE_RESULT_OUTPUT_MISSING: "missing output",              # 2
            wq.WORK_QUEUE_RESULT_STDOUT_MISSING: "no stdout",                   # 4
            wq.WORK_QUEUE_RESULT_SIGNAL: "signal received",                     # 8
            wq.WORK_QUEUE_RESULT_RESOURCE_EXHAUSTION: "exhausted resources",    # 16
            wq.WORK_QUEUE_RESULT_TASK_TIMEOUT: "time out",                      # 32
            wq.WORK_QUEUE_RESULT_UNKNOWN: "unclassified error",                 # 64
            wq.WORK_QUEUE_RESULT_FORSAKEN: "unrelated error",                   # 128
            wq.WORK_QUEUE_RESULT_MAX_RETRIES: "exceed # retries",               # 256
            wq.WORK_QUEUE_RESULT_TASK_MAX_RUN_TIME: "exceeded runtime"          # 512
    }

    def __init__(self):
        self.__exe = {}
        self.__wq = {}
        self.__taskdirs = {}
        self.__monitors = []

    def exe(self, status, taskid):
        try:
            self.__exe[status].append(taskid)
        except KeyError:
            self.__exe[status] = [taskid]

    def wq(self, status, taskid):
        for flag in ReleaseSummary.flags.keys():
            if status & flag:
                try:
                    self.__wq[flag].append(taskid)
                except KeyError:
                    self.__wq[flag] = [taskid]

    def dir(self, taskid, taskdir):
        self.__taskdirs[taskid] = taskdir

    def monitor(self, taskid):
        self.__monitors.append(taskid)

    def __str__(self):
        s = "received the following task(s):\n"
        for status in sorted(self.__exe.keys()):
            s += "returned with status {0}: {1}\n".format(status, ", ".join(self.__exe[status]))
            if status != 0:
                s += "parameters and logs in:\n\t{0}\n".format(
                        "\n\t".join([self.__taskdirs[t] for t in self.__exe[status]]))
        for flag in sorted(self.__wq.keys()):
            s += "failed due to {0}: {1}\nparameters and logs in:\n\t{2}\n".format(
                    ReleaseSummary.flags[flag],
                    ", ".join(self.__wq[flag]),
                    "\n\t".join([self.__taskdirs[t] for t in self.__wq[flag]]))
        if self.__monitors:
            s += "resource monitoring unavailable for the following tasks: {0}\n".format(", ".join(self.__monitors))
        # Trim final newline
        return s[:-1]


class TaskProvider(object):
    def __init__(self, config, interval=300):
        self.config = config
        self.basedirs = [config.base_directory, config.startup_directory]
        self.workdir = config.workdir
        self._storage = config.storage
        self._storage.activate()
        self.statusfile = os.path.join(self.workdir, 'status.yaml')

        self.parrot_path = os.path.dirname(util.which('parrot_run'))
        self.parrot_bin = os.path.join(self.workdir, 'bin')
        self.parrot_lib = os.path.join(self.workdir, 'lib')

        self.__dash = None
        self.__dash_checker = dash.TaskStateChecker(interval)

        self.__taskhandlers = {}
        self.__store = unit.UnitStore(self.config)

        self.__setup_inputs()

        create = not util.checkpoint(self.workdir, 'id')
        if create:
            self.taskid = 'lobster_{0}_{1}'.format(
                self.config.label,
                sha1(str(datetime.datetime.utcnow())).hexdigest()[-16:])
            util.register_checkpoint(self.workdir, 'id', self.taskid)
        else:
            self.taskid = util.checkpoint(self.workdir, 'id')
            util.register_checkpoint(self.workdir, 'RESTARTED', str(datetime.datetime.utcnow()))

        if self.config.advanced.use_dashboard:
            logger.info("using dashboard with task id {0}".format(self.taskid))
            monitor = dash.Monitor
        else:
            monitor = dash.DummyMonitor

        if not util.checkpoint(self.workdir, 'executable'):
            # We can actually have more than one exe name (one per task label)
            # Set 'cmsRun' if any of the tasks are of that type,
            # or use cmd command if all tasks execute the same cmd,
            # or use 'noncmsRun' if task cmds are different
            # Using this for dashboard exe name reporting
            cmsconfigs = [wflow.pset for wflow in self.config.workflows]
            cmds = [wflow.cmd for wflow in self.config.workflows]
            if any(cmsconfigs):
                exename = 'cmsRun'
            elif all(x == cmds[0] and x is not None for x in cmds):
                exename = cmds[0]
            else:
                exename = 'noncmsRun'

            util.register_checkpoint(self.workdir, 'executable', exename)

        for wflow in self.config.workflows:
            if create and not util.checkpoint(self.workdir, wflow.label):
                wflow.setup(self.workdir, self.basedirs)
                logger.info("querying backend for {0}".format(wflow.label))
                with fs.default():
                    dataset_info = wflow.dataset.get_info()

                logger.info("registering {0} in database".format(wflow.label))
                self.__store.register_dataset(wflow, dataset_info, wflow.category.runtime)
                util.register_checkpoint(self.workdir, wflow.label, 'REGISTERED')
            elif os.path.exists(os.path.join(wflow.workdir, 'running')):
                for id in self.get_taskids(wflow.label):
                    util.move(wflow.workdir, id, 'failed')

        for wflow in self.config.workflows:
            if wflow.prerequisite:
                getattr(self.config.workflows, wflow.prerequisite).register(wflow)
                if create:
                    self.__store.register_dependency(wflow.label, wflow.prerequisite, wflow.dataset.parent.total_units)

        if not util.checkpoint(self.workdir, 'sandbox cmssw version'):
            util.register_checkpoint(self.workdir, 'sandbox', 'CREATED')
            versions = set([w.version for w in self.config.workflows])
            if len(versions) == 1:
                util.register_checkpoint(self.workdir, 'sandbox cmssw version', list(versions)[0])

        if create:
            self.config.save()
            self.__dash = monitor(self.workdir)
            self.__dash.register_run()
        else:
            self.__dash = monitor(self.workdir)
            for id in self.__store.reset_units():
                self.__dash.update_task(id, dash.ABORTED)

        for p in (self.parrot_bin, self.parrot_lib):
            if not os.path.exists(p):
                os.makedirs(p)

        for exe in ('parrot_run', 'chirp', 'chirp_put', 'chirp_get'):
            shutil.copy(util.which(exe), self.parrot_bin)
            subprocess.check_call(["strip", os.path.join(self.parrot_bin, exe)])

        p_helper = os.path.join(os.path.dirname(self.parrot_path), 'lib', 'lib64', 'libparrot_helper.so')
        shutil.copy(p_helper, self.parrot_lib)

    def __find_root(self, label):
        while getattr(self.config.workflows, label).prerequisite:
            label = getattr(self.config.workflows, label).prerequisite
        return label

    def __setup_inputs(self):
        self._inputs = [
                (os.path.join(os.path.dirname(__file__), 'data', 'siteconfig'), 'siteconfig', True),
                (os.path.join(os.path.dirname(__file__), 'data', 'wrapper.sh'), 'wrapper.sh', True),
                (os.path.join(os.path.dirname(__file__), 'data', 'task.py'), 'task.py', True),
                (self.parrot_bin, 'bin', None),
                (self.parrot_lib, 'lib', None),
        ]

        # Files to make the task wrapper work without referencing WMCore
        # from somewhere else
        import WMCore
        base = os.path.dirname(WMCore.__file__)
        reqs = [
                "__init__.py",
                "__init__.pyc",
                "Algorithms",
                "Configuration.py",
                "Configuration.pyc",
                "DataStructs",
                "FwkJobReport",
                "Services/__init__.py",
                "Services/__init__.pyc",
                "Services/Dashboard",
                "WMException.py",
                "WMException.pyc",
                "WMExceptions.py",
                "WMExceptions.pyc"
                ]
        for f in reqs:
            self._inputs.append((os.path.join(base, f), os.path.join("python", "WMCore", f), True))

        if 'X509_USER_PROXY' in os.environ:
            self._inputs.append((os.environ['X509_USER_PROXY'], 'proxy', False))


    def get_taskids(self, label, status='running'):
        # Iterates over the task directories and returns all taskids found
        # therein.
        parent = os.path.join(self.workdir, label, status)
        for d in glob.glob(os.path.join(parent, '*', '*')):
            yield int(os.path.relpath(d, parent).replace(os.path.sep, ''))

    def get_report(self, label, task):
        return os.path.join(self.workdir, label, 'successful', util.id2dir(task), 'report.json')

    def obtain(self, total, tasks):
        """
        Obtain tasks from the project.

        Will create tasks for all workflows, if possible.  Merge tasks are
        always created, given enough successful tasks.  The remaining tasks
        are split proportionally between the categories based on remaining
        resources multiplied by cores used per task.  Within categories,
        tasks are created based on the same logic.

        Parameters
        ----------
            total : int
                Number of cores available.
            tasks : dict
                Dictionary with category names as keys and the number of
                tasks in the queu as values.
        """
        logger.debug("creating tasks for {} cores total".format(total))

        taskinfos = []
        sizes = {}
        wflows = {}
        for wflow in self.config.workflows:
            # First try to create a bunch of merge tasks
            taskinfos += self.__store.pop_unmerged_tasks(wflow.label, wflow.merge_size, 10)

            # Then see what we can create still in terms of work
            complete, units_left, tasks_left = self.__store.work_left(wflow.label)
            if not complete and tasks_left < 1.:
                logger.debug("workflow {} has not enough units available to form a new tasks".format(wflow.label))
                continue
            elif units_left == 0:
                continue

            cat = wflow.category.name
            if cat not in sizes:
                sizes[cat] = 0
            if cat not in wflows:
                wflows[cat] = [{}, {}]
            wflows[cat][0 if complete else 1][wflow.label] = int(math.ceil(tasks_left)) * wflow.category.cores
            sizes[cat] += int(math.ceil(tasks_left)) * wflow.category.cores

        # How many cores we need to occupy: have at least 10% of the
        # available cores provisioned with waiting work
        need = total + max(int(0.1 * total), self.config.advanced.payload)
        # Subtract all waiting cores
        for name, queued in tasks.items():
            need -= getattr(self.config.categories, name).cores * queued
        hunger = max(need, 0)

        logger.debug("need to fill {} cores".format(hunger))

        if hunger == 0:
            return []

        # Sort categories such that the smallest task limit is processed
        # first
        def helper(cat):
            return -cat.tasks * cat.cores if cat.tasks else None

        # Go through categories, adjusting number of tasks
        count = sum(sizes.values())
        for cat in sorted(self.config.categories, key=helper, reverse=True):
            if cat.name not in sizes or cat.name == 'merge':
                continue

            ccores = int(math.ceil(hunger * sizes[cat.name] / float(count)))
            if cat.tasks:
                ccores = min(ccores, (cat.tasks - tasks.get(cat.name, 0)) * cat.cores)
            ctotal = sizes[cat.name]

            logger.debug(("creating tasks for category {c.name}:" +
                    "\n\ttask limit:         {c.tasks}" +
                    "\n\ttasks in queue:     {cq}" +
                    "\n\tcores per task:     {c.cores}" +
                    "\n\tcores to fill:      {cc}" +
                    "\n\tcores able to fill: {ct}").format(
                        c=cat, cc=ccores, ct=ctotal, cq=tasks.get(cat.name, 0)))

            # Go through incomplete workflows associated with category
            for label, left in wflows[cat.name][1].items():
                if ccores > 0:
                    ntasks = max(1, int(math.ceil((ccores * left) / (float(ctotal) * cat.cores))))
                    infos = self.__store.pop_units(label, ntasks)
                else:
                    infos = []
                logger.debug("created {} tasks for workflow {}".format(len(infos), label))
                ccores -= len(infos) * cat.cores
                hunger -= len(infos) * cat.cores
                ctotal -= left
                taskinfos += infos

            # Go through complete workflows associated with category
            for label, left in wflows[cat.name][0].items():
                if ccores > 0:
                    ntasks = max(1, int(math.ceil((ccores * left) / (float(ctotal) * cat.cores))))
                    # If we should create more tasks than we can, calculate a
                    # scale factor to decrease the task size
                    taper = min(1., float(left) / (ntasks * cat.cores))
                    infos = self.__store.pop_units(label, ntasks, taper)
                else:
                    infos = []
                logger.debug("created {} tasks for workflow {}".format(len(infos), label))
                ccores -= len(infos) * cat.cores
                hunger -= len(infos) * cat.cores
                ctotal -= left
                taskinfos += infos

            count -= sizes[cat.name]

        if not taskinfos or len(taskinfos) == 0:
            return []

        tasks = []
        ids = []

        for (id, label, files, lumis, unique_arg, merge) in taskinfos:
            wflow = getattr(self.config.workflows, label)
            ids.append(id)

            jdir = util.taskdir(wflow.workdir, id)
            inputs = list(self._inputs)
            inputs.append((os.path.join(jdir, 'parameters.json'), 'parameters.json', False))
            outputs = [(os.path.join(jdir, f), f) for f in ['executable.log.gz', 'report.json']]

            monitorid, syncid = self.__dash.register_task(id)

            config = {
                'mask': {
                    'files': None,
                    'lumis': None,
                    'events': None
                },
                'monitoring': {
                    'monitorid': monitorid,
                    'syncid': syncid,
                    'taskid': self.taskid
                },
                'arguments': None,
                'output files': None,
                'want summary': True,
                'executable': None,
                'pset': None,
                'prologue': None,
                'epilogue': None
            }

            if merge:
                missing = []
                infiles = []
                inreports = []

                for task, _, _, _ in lumis:
                    report = self.get_report(label, task)
                    _, infile = list(wflow.outputs(task))[0]

                    if os.path.isfile(report):
                        inreports.append(report)
                        infiles.append((task, infile))
                    else:
                        missing.append(task)

                if len(missing) > 0:
                    template = "the following have been marked as failed because their output could not be found: {0}"
                    logger.warning(template.format(", ".join(map(str, missing))))
                    self.__store.update_missing(missing)

                if len(infiles) <= 1:
                    # FIXME report these back to the database and then skip
                    # them.  Without failing these task ids, accounting of
                    # running tasks is going to be messed up.
                    logger.debug("skipping task {0} with only one input file!".format(id))

                # takes care of the fields set to None in config
                wflow.adjust(config, jdir, inputs, outputs, merge, reports=inreports)

                files = infiles
            else:
                # takes care of the fields set to None in config
                wflow.adjust(config, jdir, inputs, outputs, merge, unique=unique_arg)

            handler = wflow.handler(id, files, lumis, jdir, merge=merge)

            # set input/output transfer parameters
            self._storage.preprocess(config, merge or wflow.prerequisite)
            # adjust file and lumi information in config, add task specific
            # input/output files
            handler.adjust(config, inputs, outputs, self._storage)

            with open(os.path.join(jdir, 'parameters.json'), 'w') as f:
                json.dump(config, f, indent=2)

            cmd = 'sh wrapper.sh python task.py parameters.json'

            tasks.append(('merge' if merge else wflow.category.name, cmd, id, inputs, outputs))

            self.__taskhandlers[id] = handler

        logger.info("creating task(s) {0}".format(", ".join(map(str, ids))))

        self.__dash.free()

        return tasks

    def release(self, tasks):
        cleanup = []
        update = defaultdict(list)
        propagate = defaultdict(dict)
        summary = ReleaseSummary()

        for task in tasks:
            self.__dash.update_task(task.tag, dash.DONE)

            handler = self.__taskhandlers[task.tag]
            failed, task_update, file_update, unit_update = handler.process(task, summary)

            wflow = getattr(self.config.workflows, handler.dataset)

            if failed:
                faildir = util.move(wflow.workdir, handler.id, 'failed')
                summary.dir(str(handler.id), faildir)
                cleanup += [lf for rf, lf in handler.outputs]
            else:
                util.move(wflow.workdir, handler.id, 'successful')

                merge = isinstance(handler, MergeTaskHandler)

                if wflow.merge_size <= 0 or merge:
                    outfn = handler.outputs[0][1]
                    outinfo = handler.output_info
                    for dep in wflow.dependents:
                        propagate[dep.label][outfn] = outinfo

                if merge and wflow.merge_cleanup:
                    files = handler.input_files
                    cleanup += files

            self.__dash.update_task(task.tag, dash.RETRIEVED)

            update[(handler.dataset, handler.unit_source)].append((task_update, file_update, unit_update))

            del self.__taskhandlers[task.tag]

        self.__dash.free()

        if len(cleanup) > 0:
            try:
                fs.remove(*cleanup)
            except (IOError, OSError):
                pass
            except ValueError as e:
                logger.error("error removing {0}:\n{1}".format(task.tag, e))

        if len(update) > 0:
            logger.info(summary)
            self.__store.update_units(update)

        for label, infos in propagate.items():
            self.__store.register_files(infos, label)

    def terminate(self):
        for id in self.__store.running_tasks():
            self.__dash.update_task(str(id), dash.CANCELLED)

    def done(self):
        left = self.__store.unfinished_units()
        return self.__store.merged() and left == 0

    def __update_dashboard(self, queue, exclude_states):
        try:
            self.__dash_checker.update_dashboard_states(self.__dash, queue, exclude_states)
        except:
            logger.warning("Could not update task states to dashboard")

    def update(self, queue):
        # update dashboard status for all unfinished tasks.
        # WAITING_RETRIEVAL is not a valid status in dashboard,
        # so skipping it for now.
        exclude_states = (dash.DONE, dash.WAITING_RETRIEVAL)
        self.__update_dashboard(queue, exclude_states)

    def update_paused(self):
        """Have the unit store updated the statistics for paused units.
        """
        self.__store.update_workflow_stats_paused()

    def tasks_left(self):
        return self.__store.estimate_tasks_left()

    def work_left(self):
        return self.__store.unfinished_units()