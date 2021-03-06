import json
import os
import sys

if len(sys.argv) < 3:
    print "usage: {0} output inputs...".format(sys.argv[0])
    sys.exit(1)

with open(sys.argv[1], 'r') as f:
    data = json.load(f)

mergedfiles = data['files']['info']
mergedkeys = dict((os.path.basename(k), k) for k in mergedfiles.keys())

data['files']['info'] = {}

for fn in sys.argv[2:]:
    print ">> merging {0}".format(fn)

    with open(fn, 'r') as f:
        report = json.load(f)

    for (ifn, (events, lumis)) in report['files']['info'].items():
        try:
            data['files']['info'][ifn][0] += events
            data['files']['info'][ifn][1].extend(lumis)
        except KeyError:
            data['files']['info'][ifn] = [events, lumis]

with open(sys.argv[1], 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
