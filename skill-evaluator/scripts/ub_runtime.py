#!/usr/bin/env python3
"""Pinned project-local UB tools and credential-free Linux execution environment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import sys
import tarfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME = ROOT / 'runs/ub-runtime-v1'
HOST_PYTHON = Path(os.environ.get('SKILL_LAB_PYTHON_PREFIX', sys.prefix)).expanduser().resolve()
RESOURCES = [
 ('tree_sitter-0.26.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl', 'https://files.pythonhosted.org/packages/8a/2f/6e6781b31677231366cb3cf27bc8269157f6d4b03c9032865a4f5f2bbe7e/tree_sitter-0.26.0-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl', '5a6b333b0282d8bb0af741f9b018bd2523d4eecb2686bf6717066a625fecfaa4', 667487),
 ('tree_sitter_language_pack-1.23.1-cp310-abi3-manylinux_2_34_x86_64.whl', 'https://files.pythonhosted.org/packages/9c/21/c84919f91a07cfb135469d59b37c93b68970bf279bce71d67a18c85ee9bf/tree_sitter_language_pack-1.23.1-cp310-abi3-manylinux_2_34_x86_64.whl', '0091c27b7f9d625d1181582abafaae8a0f3d0aa9d39c2d2048016ffbb0e17790', 2452822),
 ('coverage-7.17.0-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl', 'https://files.pythonhosted.org/packages/91/ac/eb4c31f741509a9ef48cf6f28385e98e9de0785599282252eee252ff8af7/coverage-7.17.0-cp312-cp312-manylinux1_x86_64.manylinux_2_28_x86_64.manylinux_2_5_x86_64.whl', '85932227e9dd38e62cd29c17fefdfecfb919eead6af1e3259522d03cf85de983', 261789),
 ('parsers.json','https://github.com/xberg-io/tree-sitter-language-pack/releases/download/v1.23.1/parsers.json','434b348fac55bd853aec716cee0f7996d4dd0dac783a4a1cdee523d7f5e97231',30054),
 ('parsers-linux-x86_64.tar.zst','https://github.com/xberg-io/tree-sitter-language-pack/releases/download/v1.23.1/parsers-linux-x86_64.tar.zst','e7aa7f5ee7d6b0310f25b684748c3c9a6ee4272288d2cf30552dae20ef637c16',23190088),
]
LIMITS = {'memory_bytes': 1024**3, 'cpu_seconds': 90, 'file_bytes': 32*1024**2,
          'output_bytes': 256*1024, 'processes': 128, 'wall_seconds': 120}


def sha(raw): return hashlib.sha256(raw).hexdigest()
def dump(path, value): Path(path).write_text(json.dumps(value, sort_keys=True, indent=2)+'\n')
def require(ok, message):
    if not ok: raise ValueError(message)


def fetch(out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    for name,url,digest,size in RESOURCES:
        path=out/name
        if path.exists():
            require(path.is_file() and not path.is_symlink() and path.stat().st_size==size and sha(path.read_bytes())==digest, 'Existing download differs: '+name)
            continue
        request=urllib.request.Request(url,headers={'User-Agent':'skill-eval-kit-offline-provisioner'})
        with urllib.request.urlopen(request,timeout=30) as response:
            data=response.read(size+1)
        require(len(data)==size and sha(data)==digest,'Downloaded bytes differ: '+name)
        with path.open('xb') as handle: handle.write(data)
    dump(out/'inventory.json',[{'file':n,'url':u,'sha256':h,'bytes':s} for n,u,h,s in RESOURCES])


def regular_relative(name):
    path=Path(name)
    require(not path.is_absolute() and '..' not in path.parts and '\\' not in name, 'Unsafe archive path')
    return path


def unpack_wheel(path, target):
    with zipfile.ZipFile(path) as archive:
        require(sum(i.file_size for i in archive.infolist()) <= 128*1024**2, 'Wheel decompression bound')
        for member in archive.infolist():
            relative=regular_relative(member.filename)
            require((member.external_attr >> 16) & 0o170000 != 0o120000,'Wheel symlink refused')
            if member.is_dir(): continue
            require(not any(part.endswith('.data') for part in relative.parts), 'Unsupported wheel .data layout')
            destination=target/relative; destination.parent.mkdir(parents=True,exist_ok=True)
            require(not destination.exists(), 'Overlapping wheel files')
            destination.write_bytes(archive.read(member))


def provision(runtime=DEFAULT_RUNTIME):
    runtime=Path(runtime).resolve(); runtime.mkdir(parents=True,exist_ok=True)
    require(not (runtime/'runtime.json').exists(),'Already provisioned; validate existing runtime')
    downloads=runtime/'downloads'; fetch(downloads)
    py=runtime/'python'; (py/'bin').mkdir(parents=True,exist_ok=False)
    shutil.copy2(HOST_PYTHON/'bin/python3.12',py/'bin/python3.12')
    shutil.copytree(HOST_PYTHON/'lib/python3.12',py/'lib/python3.12',
                    ignore=shutil.ignore_patterns('site-packages','__pycache__','*.pyc'))
    # Copy only host-distribution shared libraries referenced by stdlib extensions.
    libraries=set()
    for extension in (py/'lib/python3.12/lib-dynload').glob('*.so'):
        original=HOST_PYTHON/'lib/python3.12/lib-dynload'/extension.name
        output=subprocess.run(['/usr/bin/ldd',str(original)],capture_output=True,text=True,check=True).stdout
        libraries.update(re.findall(r'=> (' + re.escape(str(HOST_PYTHON)) + r'/[^ ]+)',output))
    for library in sorted(libraries):
        shutil.copy2(Path(library).resolve(),py/'lib'/Path(library).name)
    for name,body in [('python3','exec /runtime/bin/python3.12 -S "$@"'),('python','exec /runtime/bin/python3.12 -S "$@"')]:
        path=py/'bin'/name; path.write_text('#!/bin/sh\n'+body+'\n');path.chmod(0o755)
    for name in ['crapper','dryer','mutator']:
        path=py/'bin'/name; path.write_text('#!/bin/sh\nexec /runtime/bin/python3 -m '+name+' "$@"\n');path.chmod(0o755)
    deps=runtime/'deps';deps.mkdir()
    for path in downloads.glob('*.whl'):unpack_wheel(path,deps)
    inventory=json.loads((ROOT/'evidence/stock-review/inventory.json').read_text())
    for item in inventory:
        raw=(ROOT/'evidence/stock-review'/item['local_file']).read_bytes()
        require(sha(raw)==item['sha256'],'Changed companion snapshot')
        require(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()==item['git_blob_sha1'],'Changed Git blob')
        component=item['repository'].split('/')[-1]
        dest=runtime/'companions'/component/regular_relative(item['path'])
        dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
    # A trusted local zstd executable decodes the pinned archive; stream only one bounded regular library.
    process=subprocess.Popen(['/usr/bin/bwrap','--unshare-all','--die-with-parent','--cap-drop','ALL','--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64','--ro-bind',str(downloads/'parsers-linux-x86_64.tar.zst'),'/archive.zst','--','/usr/bin/zstd','-d','-c','/archive.zst'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env={})
    library=None; total=0
    try:
        with tarfile.open(fileobj=process.stdout,mode='r|') as archive:
            for item in archive:
                regular_relative(item.name);total+=item.size
                require(total<=1024**3,'Parser archive decompression bound')
                if Path(item.name).name!='libtree_sitter_python.so':continue
                require(library is None and item.isfile() and item.size<=16*1024**2,'Invalid Python parser member')
                library=archive.extractfile(item).read()
        require(process.wait(timeout=10)==0,'Parser decompression failed')
    finally:
        if process.poll() is None:process.kill();process.wait()
        process.stdout.close()
    require(library is not None,'Missing Python parser')
    cache=runtime/'parser-cache/tree-sitter-language-pack/v1.23.1/libs';cache.mkdir(parents=True,mode=0o700);cache.chmod(0o700)
    (cache/'libtree_sitter_python.so').write_bytes(library)
    files={p.relative_to(runtime).as_posix():sha(p.read_bytes()) for folder in ['python','deps','companions','parser-cache'] for p in (runtime/folder).rglob('*') if p.is_file()}
    receipt={'schema':1,'classification':'project-local original UB companion runtime; no model calls',
             'files':files,'parser_sha256':sha(library),'resources':[{'file':n,'sha256':h,'bytes':s} for n,u,h,s in RESOURCES],
             'limits':LIMITS,'limit_scope':'per-process memory/CPU, per-file bytes; wall/output bound per command; no aggregate disk/cgroup bound'}
    dump(runtime/'runtime.json',receipt);return receipt


def validate(runtime=DEFAULT_RUNTIME):
    runtime=Path(runtime).resolve();receipt=json.loads((runtime/'runtime.json').read_text())
    expected=set(receipt['files'])
    actual={p.relative_to(runtime).as_posix() for folder in ['python','deps','companions','parser-cache'] for p in (runtime/folder).rglob('*') if p.is_file()}
    require(actual==expected,'Runtime inventory differs')
    for name,digest in receipt['files'].items():
        path=runtime/name
        require(path.is_file() and not path.is_symlink() and sha(path.read_bytes())==digest,'Runtime file changed: '+name)
    return receipt


def sandbox_command(work, command, origin=None, runtime=DEFAULT_RUNTIME):
    runtime=Path(runtime).resolve(); work=Path(work).resolve()
    require(work.is_dir(),'Missing work directory')
    require(isinstance(command,list) and command and all(isinstance(s,str) and s for s in command),'Expected argv')
    cmd=['/usr/bin/bwrap','--unshare-all','--die-with-parent','--new-session','--cap-drop','ALL',
         '--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64','--symlink','usr/bin','/bin',
         '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--clearenv',
         '--ro-bind',str(runtime/'python'),'/runtime','--ro-bind',str(runtime/'deps'),'/deps/python',
         '--ro-bind',str(runtime/'companions'),'/deps/companions','--ro-bind',str(runtime/'parser-cache'),'/parser-cache',
         '--bind',str(work),'/work','--chdir','/work']
    if origin is not None:cmd+=['--bind',str(Path(origin).resolve()),'/origin.git']
    env={'PATH':'/runtime/bin:/usr/bin','PYTHONHOME':'/runtime','PYTHONPATH':'/deps/python:/deps/companions/crapper/src:/deps/companions/dryer/src:/deps/companions/mutator/src',
         'PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1','HOME':'/tmp/home','TMPDIR':'/tmp',
         'TREE_SITTER_LANGUAGE_PACK_CACHE_DIR':'/parser-cache','GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':'/dev/null',
         'LANG':'C.UTF-8'}
    for name,value in env.items():cmd+=['--setenv',name,value]
    bootstrap="import os,resource,sys;resource.setrlimit(resource.RLIMIT_NPROC,(128,128));os.execvp(sys.argv[1],sys.argv[1:])"
    return cmd+['--','/runtime/bin/python3','-c',bootstrap,*command]


def process_limits():
    for kind,value in [(resource.RLIMIT_CPU,LIMITS['cpu_seconds']), (resource.RLIMIT_AS,LIMITS['memory_bytes']),
                       (resource.RLIMIT_FSIZE,LIMITS['file_bytes']), (resource.RLIMIT_NOFILE,256),(resource.RLIMIT_CORE,0)]:
        resource.setrlimit(kind,(value,value))


def bounded(command,wall=120,stdin=b''):
    require(isinstance(stdin,bytes) and len(stdin)<=65536,'Input too large or not bytes')
    require(type(wall) in (int,float) and 0<wall<=300,'Wall limit must be within 0..300 seconds')
    start=time.monotonic();streams={'stdout':bytearray(),'stderr':bytearray()};reason=None
    process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={},start_new_session=True,preexec_fn=process_limits)
    selector=selectors.DefaultSelector()
    try:
        pending=memoryview(stdin)
        if pending:
            os.set_blocking(process.stdin.fileno(),False);selector.register(process.stdin,selectors.EVENT_WRITE,'stdin')
        else:process.stdin.close()
        for stream,name in [(process.stdout,'stdout'),(process.stderr,'stderr')]:
            os.set_blocking(stream.fileno(),False);selector.register(stream,selectors.EVENT_READ,name)
        while selector.get_map() or process.poll() is None:
            if time.monotonic()-start>wall:reason='timeout';break
            for key,_ in selector.select(.05):
                if key.data=='stdin':
                    try:pending=pending[os.write(key.fileobj.fileno(),pending[:4096]):]
                    except BrokenPipeError:pending=memoryview(b'')
                    if not pending:selector.unregister(key.fileobj);key.fileobj.close()
                    continue
                data=os.read(key.fileobj.fileno(),4096)
                if not data:selector.unregister(key.fileobj);continue
                room=LIMITS['output_bytes']-sum(map(len,streams.values()))
                streams[key.data].extend(data[:max(0,room)])
                if len(data)>room:reason='output_limit';break
            if reason:break
    finally:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        process.wait();selector.close();process.stdin.close();process.stdout.close();process.stderr.close()
    return {'command':command,'status':reason or ('completed' if process.returncode==0 else 'process_error'),
            'exit_code':process.returncode,'wall_seconds':time.monotonic()-start,
            **{name:data.decode(errors='replace') for name,data in streams.items()},'cleanup':'process group killed; namespace init reaped'}


def rehearse(out, runtime=DEFAULT_RUNTIME):
    """Execute unchanged tools against a fixed authored task plus failure controls."""
    validate(runtime)
    out=Path(out).resolve();out.mkdir(parents=True,exist_ok=False)
    report={'status':'incomplete','classification':'authored actual toolchain controls; no model/skill performance claim','model_calls':0,'processes':[]}
    dump(out/'report.json',report)
    work=out/'work';(work/'src').mkdir(parents=True);(work/'tests').mkdir()
    source='def normalize(value):\n    if not isinstance(value, str):\n        raise TypeError("string required")\n    return value.strip().lower()\n'
    tests='import unittest\nfrom src.solution import normalize\n\nclass Tests(unittest.TestCase):\n    def test_value(self):\n        self.assertEqual(normalize(" A "), "a")\n        self.assertEqual(normalize(""), "")\n    def test_type(self):\n        with self.assertRaises(TypeError): normalize(None)\n'
    (work/'src/solution.py').write_text(source);(work/'tests/test_solution.py').write_text(tests)
    for name in ['src/__init__.py','tests/__init__.py']:(work/name).write_text('')
    def execute(label, directory, argv):
        result=bounded(sandbox_command(directory,argv,runtime=runtime))
        dump(out/(label+'.json'),result);report['processes'].append({'label':label,'status':result['status'],'exit_code':result['exit_code']})
        return result
    try:
        result=execute('parser',work,['python3','-c',"from tree_sitter_language_pack import download,get_parser;download(['python']);print(get_parser('python').parse(b'def f(x): return x+1').root_node)"])
        require(result['status']=='completed' and 'function_definition' in result['stdout'],'Parser preflight failed')
        result=execute('test',work,['python3','-m','unittest','discover','-s','tests','-v'])
        require(result['status']=='completed' and 'Ran 2 tests' in result['stderr'],'Nonempty actual test suite failed')
        result=execute('crapper',work,['crapper','--root','/work','--threshold','10','/work/src'])
        require(result['status']=='completed' and 'Ran 2 tests' in result['stderr'] and '100.0%' in result['stdout'],'Crapper actual coverage failed')
        require((work/'target/coverage/python/lcov.info').is_file(),'Missing actual coverage artifact')
        result=execute('dryer',work,['dryer','--root','/work','--threshold','0.8','/work/src'])
        require(result['status']=='completed' and 'No duplicate candidates found.' in result['stdout'],'Dryer clean control failed')
        result=execute('mutator',work,['mutator','--root','/work','--mutate-all','--max-workers','1','/work/src'])
        require(result['status']=='completed' and re.search(r'Total\s+1\s+0\s+0\s+1\s+100.0%',result['stdout']),'Nonempty mutation control failed')
        duplicate=out/'duplicate';shutil.copytree(work,duplicate)
        (duplicate/'src/duplicate.py').write_text(source.replace('normalize','normalize_again'))
        result=execute('dryer-positive',duplicate,['dryer','--root','/work','--threshold','0.8','/work/src'])
        require(result['status']=='completed' and 'DUPLICATE score=1.00' in result['stdout'],'Dryer planted duplicate missed')
        bad=out/'known-mutant';shutil.copytree(work,bad)
        (bad/'src/solution.py').write_text(source.replace('if not isinstance','if isinstance'))
        result=execute('known-mutant',bad,['python3','-m','unittest','discover','-s','tests','-v'])
        require(result['status']=='process_error' and result['exit_code']==1 and 'Ran 2 tests' in result['stderr'] and 'FAILED (errors=2)' in result['stderr'],'Semantic mutant did not fail as expected')
        require((work/'src/solution.py').read_text()==source,'Companion changed original source')
        report.update(status='pass',tests_executed=2,mutations_executed=1,mutations_killed=1,
                      duplicate_control='detected 1.00',semantic_failure_control='two runtime test errors; not timeout/compile failure',
                      runtime_sha256=sha((Path(runtime)/'runtime.json').read_bytes()))
    except (OSError,ValueError) as error:report.update(status='fail',error=str(error))
    dump(out/'report.json',report);return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ['provision','validate','rehearse','exec']:
        command=commands.add_parser(name);command.add_argument('--runtime',default=str(DEFAULT_RUNTIME))
        if name=='rehearse':command.add_argument('--out',required=True)
        if name=='exec':
            command.add_argument('--work',required=True);command.add_argument('--origin');command.add_argument('--wall',type=float,default=120);command.add_argument('argv',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    if args.command=='provision': result=provision(args.runtime);result={k:v for k,v in result.items() if k!='files'}
    elif args.command=='validate':result={'status':'pass','files':len(validate(args.runtime)['files'])}
    elif args.command=='rehearse':result=rehearse(args.out,args.runtime)
    else:
        validate(args.runtime);argv=args.argv[1:] if args.argv[:1]==['--'] else args.argv
        result=bounded(sandbox_command(args.work,argv,args.origin,args.runtime),args.wall)
    print(json.dumps(result,indent=2));return 0 if result.get('status','completed') in {'pass','completed'} else 1


if __name__=='__main__':raise SystemExit(main())
