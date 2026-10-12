"""Structured verification without model credentials or external services."""
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT))
os.chdir(ROOT)
start=time.monotonic()
suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),top_level_dir=str(ROOT))
result=unittest.TextTestRunner(verbosity=1).run(suite)
lint=subprocess.run([sys.executable,'-m','ruff','check','src','tests','scripts'],capture_output=True,text=True,timeout=30)
report={'successful':result.wasSuccessful() and lint.returncode==0,'tests':result.testsRun,
        'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
        'lint_exit':lint.returncode,'lint_output':lint.stdout+lint.stderr,'python':sys.version,
        'platform':platform.platform(),'duration_seconds':time.monotonic()-start,
        'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'dirty':bool(subprocess.check_output(['git','status','--porcelain'],text=True)),
        'details':[(str(t),detail) for t,detail in result.failures+result.errors]}
(ROOT/'.artifacts').mkdir(exist_ok=True)
(ROOT/'.artifacts'/'check-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('details','lint_output')},ensure_ascii=False))
raise SystemExit(0 if report['successful'] else 1)
