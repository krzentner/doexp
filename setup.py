# This setup.py exists purely to allow editable installs
from setuptools import setup
from setuptools import find_packages

setup(
    name='doexp',
    package_dir={'': 'src'},
    packages=find_packages(where='src'),
    scripts=['scripts/doexp'],
    install_requires=['psutil'],
    )
