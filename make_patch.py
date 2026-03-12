"""
生成更新补丁
============
用法: python make_patch.py

将 config.py, core/, gui/, utils/ 等应用代码打包为 patch.zip。
用户解压到 exe 同级目录即可完成更新 (会生成 patch/ 文件夹)。
"""

import os
import zipfile
import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))

APP_DIRS = ['core', 'gui', 'utils']
APP_FILES = ['config.py', 'main.py']
RESOURCE_DIRS = ['img']
RESOURCE_FILES = [
    os.path.join('yolo', 'runs', 'fish_detect', 'weights', 'best.pt'),
    os.path.join('imitation', 'policy.pt'),
]

EXCLUDE = {'__pycache__', '.pyc', '.pyo', '.bak'}


def should_include(path):
    for ex in EXCLUDE:
        if ex in path:
            return False
    return True


def make_patch():
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_name = f'patch_{timestamp}.zip'
    zip_path = os.path.join(ROOT, zip_name)

    count = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for fname in APP_FILES:
            src = os.path.join(ROOT, fname)
            if os.path.isfile(src):
                zf.write(src, os.path.join('patch', fname))
                count += 1

        for dname in APP_DIRS:
            src_dir = os.path.join(ROOT, dname)
            if not os.path.isdir(src_dir):
                continue
            for dirpath, _, filenames in os.walk(src_dir):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    if not should_include(full):
                        continue
                    if not fn.endswith('.py'):
                        continue
                    rel = os.path.relpath(full, ROOT)
                    zf.write(full, os.path.join('patch', rel))
                    count += 1

        for dname in RESOURCE_DIRS:
            src_dir = os.path.join(ROOT, dname)
            if not os.path.isdir(src_dir):
                continue
            for dirpath, _, filenames in os.walk(src_dir):
                for fn in filenames:
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, ROOT)
                    zf.write(full, os.path.join('patch', rel))
                    count += 1

        for rel_path in RESOURCE_FILES:
            src = os.path.join(ROOT, rel_path)
            if not os.path.isfile(src):
                continue
            if not should_include(src):
                continue
            zf.write(src, os.path.join('patch', rel_path))
            count += 1

    size_kb = os.path.getsize(zip_path) / 1024
    print(f'补丁已生成: {zip_name}')
    print(f'  包含 {count} 个文件, {size_kb:.0f} KB')
    print()
    print('使用方法:')
    print('  将 zip 解压到 VRChat钓鱼助手.exe 同级目录')
    print('  确保生成了 patch/ 文件夹即可')
    print('  若 zip 中包含 yolo/runs/.../best.pt，则会优先覆盖内置模型')


if __name__ == '__main__':
    make_patch()
