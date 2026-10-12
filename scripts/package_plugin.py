"""Build a plugin-only archive with an explicit allowlist, never runtime data."""
from pathlib import Path
import zipfile

root=Path(__file__).resolve().parents[1]
files=[root/name for name in ('plugin.json','mcp.json','LICENSE','README.md','README.zh-CN.md')]
files+=sorted((root/'skills').rglob('*.md'))
files+=sorted((root/'docs').glob('*.md'))
out=root/'dist'/'feishu-agent-bridge-plugin-0.1.0.zip'
out.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as archive:
    for file in files:
        archive.write(file,file.relative_to(root))
print(out)
