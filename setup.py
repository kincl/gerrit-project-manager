from setuptools import setup

setup(name='gerrit-project-manager',
      version='0.1',
      description='Manages Gerrit Projects',
      url='https://gerrit.ccs.ornl.gov/#/admin/projects/infra/gerrit-project-manager',
      author='Jason Kincl',
      author_email='kincljc@ornl.gov',
      license='Unknown',
      packages=['gerrit_projects'],
      entry_points= { 'console_scripts': ['gerrit-projects=gerrit_projects.projects:main'] })
