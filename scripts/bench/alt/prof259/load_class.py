"""Load scratch .cls files into SNB1B (not the whole tree).

    python -m scripts.bench.alt.prof259.load_class FILE [FILE...]
    python -m scripts.bench.alt.prof259.load_class --delete PACKAGE
"""

import sys

import iris

conn = iris.connect("localhost", 31972, "SNB1B", "_SYSTEM", "SYS")
irisobj = iris.createIRIS(conn)
args = sys.argv[1:]
if args and args[0] == "--delete":
    print(irisobj.classMethodValue("%SYSTEM.OBJ", "DeletePackage", args[1], "-d"))
    sys.exit(0)
for path in args:
    dest = "/tmp/zivgp_" + path.replace("/", "_")
    stream = irisobj.classMethodObject("%Stream.FileCharacter", "%New")
    stream.invokeVoid("LinkToFile", dest)
    for line in open(path).read().split("\n"):
        stream.invokeVoid("WriteLine", line)
    stream.invokeVoid("%Save")
    res = irisobj.classMethodValue("%SYSTEM.OBJ", "Load", dest, "ck-d")
    ok = irisobj.classMethodValue("%SYSTEM.Status", "IsOK", res)
    print(path, "OK" if ok else irisobj.classMethodValue("%SYSTEM.Status", "GetOneErrorText", res))
