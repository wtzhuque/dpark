import os
import atexit
import optparse

from rdd import *
from schedule import *
from env import env
from broadcast import Broadcast

class DparkContext:
    nextRddId = 0
    nextShuffleId = 0

    def __init__(self, master=None, name=None):
        if 'MESOS_SLAVE_PID' in os.environ:
            from executor import run
            run()
            sys.exit(0)
        
        options = parse_options()
        if master is None:
            master = options.master
        if name is None:
            name = options.name

        self.master = master
        self.name = name

        if self.master.startswith('local'):
            self.scheduler = LocalScheduler()
            self.isLocal = True
        elif self.master.startswith('thread'):
            self.scheduler = MultiThreadScheduler(options.parallel)
            self.isLocal = True
        elif self.master.startswith('process'):
            self.scheduler = MultiProcessScheduler(options.parallel)
            self.isLocal = False
        elif (self.master.startswith('mesos')
              or self.master.startswith('zoo')):
            if '://' not in self.master:
                self.master = os.environ.get('MESOS_MASTER')
                if not self.master:
                    raise ValueError("mesos master url needed")
            self.scheduler = MesosScheduler(self.master, options) 
            self.isLocal = False
        
        if options.parallel:
            self.defaultParallelism = options.parallel
        else:
            self.defaultParallelism = self.scheduler.defaultParallelism()
        self.defaultMinSplits = max(self.defaultParallelism, 2)
       
        self.profile = options.profile
        if self.profile:
            import yappi
            yappi.start()

        env.start(True)
        self.scheduler.start()
        self.started = True
        atexit.register(self.stop)

    def newRddId(self):
        self.nextRddId += 1
        return self.nextRddId

    def newShuffleId(self):
        self.nextShuffleId += 1
        return self.nextShuffleId

    def parallelize(self, seq, numSlices=None): 
        if numSlices is None:
            numSlices = self.defaultParallelism
        return ParallelCollection(self, seq, numSlices)

    def makeRDD(self, seq, numSlices=None):
        if numSlices is None:
            numSlices = self.defaultParallelism
        return self.parallelize(seq, numSlices)
    
    def textFile(self, path, numSplits=None, splitSize=None,
            ext='', followLink=True, maxdepth=0, cls=TextFileRDD):
        if os.path.isdir(path):
            paths = []
            for root,dirs,names in os.walk(path, followlinks=followLink):
                if maxdepth > 0:
                    depth = len(filter(None, root[len(path):].split('/'))) + 1
                    if depth > maxdepth:
                        break
                for n in names:
                    if n.endswith(ext) and not n.startswith('.'):
                        p = os.path.join(root, n)
                        if followLink or not os.path.islink(p):
                            paths.append(p)
                for d in dirs[:]:
                    if d.startswith('.'):
                        dirs.remove(d)

            rdds = [cls(self, p, numSplits,splitSize) 
                     for p in paths]
            return self.union(rdds)
        else:
            return cls(self, path, numSplits, splitSize)

    def csvFile(self, *args, **kwargs):
        return self.textFile(cls=CSVFileRDD, *args, **kwargs)

#    def objectFile(self, path, minSplits=None):
#        if minSplits is None:
#            minSplits = self.defaultMinSplits
#        return self.sequenceFile(path, minSplits).flatMap(lambda x: loads(x))

    def union(self, rdds):
        return UnionRDD(self, rdds)

    def accumulator(self, init, param=None):
        return Accumulator(init, param)

    def broadcast(self, v):
        return Broadcast.newBroadcast(v, self.isLocal)

    def stop(self):
        if not self.started:
            return

        self.scheduler.stop()
        env.stop()
        self.started = False

        if self.profile:
            import yappi
            yappi.print_stats()

    def waitForRegister(self):
        self.scheduler.waitForRegister()

    def runJob(self, rdd, func, partitions=None, allowLocal=False):
        if partitions is None:
            partitions = range(len(rdd))
        return self.scheduler.runJob(rdd, func, partitions, allowLocal)

    def __getstate__(self):
        raise ValueError("should not pickle ctx")

def parse_options():
    parser = optparse.OptionParser(usage="Usage: %prog [options] [args]")
    parser.allow_interspersed_args=False

    parser.add_option("-m", "--master", type="string", default="local")
    parser.add_option("-n", "--name", type="string", default="dpark")
    parser.add_option("-p", "--parallel", type="int", default=0)

    parser.add_option("-c", "--cpus", type="float", default=1.0,
                    help="cpus used per task")
    parser.add_option("--mem", type="float", default=100.0,
                    help="memory used per task")

    parser.add_option("--self", action="store_true",
        help="user self as exectuor")
    parser.add_option("--profile", action="store_true",
        help="do profile using yappi")

    parser.add_option("-q", "--quiet", action="store_true")
    parser.add_option("-v", "--verbose", action="store_true")
    options, args = parser.parse_args()
    
    logging.basicConfig(format='[dpark] %(asctime)-15s %(message)s',
        level=options.quiet and logging.ERROR
              or options.verbose and logging.DEBUG or logging.INFO)
    
    return options