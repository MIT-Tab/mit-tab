#!/usr/bin/env python

from setuptools import find_packages
from setuptools import setup

setup(
    name='mittab',
    version='2.0.0',
    description='A web application to tabulate and run APDA tournaments',
    author='Joseph Lynch',
    author_email='jolynch@mit.edu',
    url='https://github.com/jolynch/mit-tab.git',
    packages=find_packages(exclude=['tests']),
    include_package_data=True,
    setup_requires=['setuptools'],
    install_requires=[
        'Django=1.4.5',
        'South',
        'mock'
        'pytest'
    ],
    license='MIT License'
)
