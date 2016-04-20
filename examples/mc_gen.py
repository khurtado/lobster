from lobster import cmssw
from lobster.core import *

version = '_take29'

storage = StorageConfiguration(
        output=[
            "hdfs:///store/user/matze/test_mc" + version,
            "file:///hadoop/store/user/matze/test_mc" + version,
            "root://deepthought.crc.nd.edu//store/user/matze/test_mc" + version,
            "chirp://earth:9666/test_mc" + version,
            "srm://T3_US_NotreDame/store/user/matze/test_mc" + version,
        ]
)

workflows = []

lhe = Workflow(
        label='lhe_step',
        pset='mc_gen/HIG-RunIIWinter15wmLHE-00196_1_cfg.py',
        sandbox_release='mc_gen/CMSSW_7_1_16_patch1',
        merge_size='125M',
        dataset=ProductionDataset(
            events_per_task=250,
            events_per_lumi=25,
            number_of_tasks=100
        ),
        category=Category(
            name='lhe',
            cores=1,
            memory=2000
        )
)

gs = Workflow(
        label='gs_step',
        pset='mc_gen/HIG-RunIISummer15GS-00177_1_cfg.py',
        sandbox_release='mc_gen/CMSSW_7_1_18',
        merge_size='500M',
        dataset=ParentDataset(
            parent=lhe,
            units_per_task=1
        ),
        category=Category(
            name='gs',
            cores=1,
            memory=2000,
            runtime=45 * 60
        )
)

digi = Workflow(
        label='digi_step',
        pset='mc_gen/HIG-RunIIFall15DR76-00243_1_cfg.py',
        sandbox_release='mc_gen/CMSSW_7_6_1',
        merge_size='1G',
        dataset=ParentDataset(
            parent=gs,
            units_per_task=10
        ),
        category=Category(
            name='digi',
            cores=1,
            memory=2600,
            runtime=45 * 60,
        )
)

reco = Workflow(
        label='reco_step',
        pset='mc_gen/HIG-RunIIFall15DR76-00243_2_cfg.py',
        sandbox_release='mc_gen/CMSSW_7_6_1',
        # Explicitly specify outputs, since the dependency processing only
        # works for workflows with one output file, but the configuration
        # includes two.
        outputs=['HIG-RunIIFall15DR76-00243.root'],
        merge_size='1G',
        dataset=ParentDataset(
            parent=digi,
            units_per_task=6
        ),
        category=Category(
            name='reco',
            cores=4,
            memory=2800,
            runtime=45 * 60
        )
)

maod = Workflow(
        label='mAOD_step',
        pset='mc_gen/HIG-RunIIFall15MiniAODv2-00224_1_cfg.py',
        sandbox_release='mc_gen/CMSSW_7_6_3',
        merge_size='500M',
        dataset=ParentDataset(
            parent=reco,
            units_per_task=60
        ),
        category=Category(
            name='mAOD',
            cores=2,
            memory=2000,
            runtime=30 * 60
        )
)

config = Config(
        label='shuffle',
        workdir='/tmpscratch/users/matze/test_mc' + version,
        plotdir='/afs/crc.nd.edu/user/m/mwolf3/www/lobster/test_mc' + version,
        storage=storage,
        workflows=[lhe, gs, digi, reco, maod],
        advanced=AdvancedOptions(log_level=1)
)