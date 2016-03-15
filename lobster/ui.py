from argparse import ArgumentParser
import logging
import os
import sys
import yaml

# FIXME pycurl shipping with CMSSW is too old to harmonize with modern DBS!
rm = []
for f in sys.path:
    if '/cvmfs' in f:
        for pkg in ('pycurl', 'numpy', 'matplotlib'):
            if pkg in f:
                rm.append(f)
for f in rm:
    sys.path.remove(f)

from lobster.core import command, config, legacy
from lobster import util

logger = logging.getLogger('lobster')

def boil():
    parser = ArgumentParser(description='A task submission tool for CMS')
    parser.add_argument('--verbose', '-v', action='count', default=0, help='increase verbosity')
    parser.add_argument('--quiet', '-q', action='count', default=0, help='decrease verbosity')

    command.Command.register([os.path.join(os.path.dirname(__file__), d, 'commands') for d in ['.', 'cmssw']], parser)

    parser.add_argument(metavar='{configfile,workdir}', dest='checkpoint',
            help='configuration file to use or working directory to resume.')

    args = parser.parse_args()

    if os.path.isfile(args.checkpoint):
        configfile = args.checkpoint
        if configfile.endswith('.yaml') or configfile.endswith('.yml'):
            with open(configfile) as f:
                cfg = legacy.pythonize_yaml(yaml.load(f))
        else:
            import imp
            cfg = imp.load_source('userconfig', configfile).config

        if util.checkpoint(cfg.workdir, 'version'):
            cfg = config.Config.load(cfg.workdir)
        else:
            # This is the original configuration file!
            cfg.base_directory = os.path.abspath(os.path.dirname(configfile))
            cfg.base_configuration = os.path.abspath(configfile)
            cfg.startup_directory = os.path.abspath(os.getcwd())
    else:
        # Load configuration from working directory passed to us
        workdir = args.checkpoint
        try:
            cfg = config.Config.load(workdir)
        except Exception as e:
            parser.error("the working directory '{0}' does not contain a valid configuration: {1}".format(workdir, e))
        cfg.workdir = workdir
    args.config = cfg

    # Handle logging for everything in only one place!
    level = max(1, args.config.advanced.log_level + args.quiet - args.verbose) * 10
    logger.setLevel(level)

    formatter = logging.Formatter(fmt='%(asctime)s [%(levelname)s] %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    logger.addHandler(console)

    if args.plugin.daemonizable:
        fn = args.plugin.__class__.__name__.lower() + '.log'
        logger.info("saving log to {0}".format(os.path.join(cfg.workdir, fn)))
        if not os.path.isdir(cfg.workdir):
            os.makedirs(cfg.workdir)
        fileh = logging.handlers.RotatingFileHandler(os.path.join(cfg.workdir, fn), maxBytes=500e6, backupCount=10)
        fileh.setFormatter(formatter)
        args.preserve = fileh.stream
        logger.addHandler(fileh)

        if not getattr(args, "foreground", False):
            logger.removeHandler(console)

    args.plugin.run(args)
