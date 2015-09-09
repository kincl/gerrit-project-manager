from setuptools import setup

setup(name='gerrit-project-manager',
      version='0.1',
      description='Manages Gerrit Projects',
      url='https://github.com/kincl/gerrit-project-manager',
      author='Jason Kincl',
      author_email='jkincl@gmail.com',
      license='Unknown',
      packages=['gerrit_projects'],
      install_requires=['paramiko'],
      entry_points= { 'console_scripts': ['gerrit-projects=gerrit_projects.projects:main'] })
