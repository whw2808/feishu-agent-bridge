"""Stage and archive only portable plugin files; never copy a developer worktree."""
from pathlib import Path
import shutil
import zipfile


def build(root):
    root = Path(root).resolve()
    files = [root/name for name in ('plugin.json','mcp.json','LICENSE','README.md','README.zh-CN.md')]
    files += sorted((root/'skills').rglob('*.md'))
    files += sorted((root/'docs').glob('*.md'))
    if any(file.is_symlink() or not file.resolve().is_relative_to(root) for file in files):
        raise ValueError('plugin sources must be ordinary files within the repository')
    stage = root/'dist'/'plugin'
    if stage.is_symlink():
        raise ValueError('plugin stage must not be a symlink')
    stage.parent.mkdir(exist_ok=True)
    if stage.exists():
        shutil.rmtree(stage)  # This path is exclusively the generated plugin stage.
    stage.mkdir()
    for file in files:
        target = stage/file.relative_to(root)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(file,target)
    out = root/'dist'/'feishu-agent-bridge-plugin-0.1.0.zip'
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(stage/file.relative_to(root),file.relative_to(root))
    return stage,out


if __name__=='__main__':
    stage,archive = build(Path(__file__).resolve().parents[1])
    print(stage)
    print(archive)
