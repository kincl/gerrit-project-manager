gerrit-project-manager
======================

Manages projects in Gerrit


Initial codebase imported from https://github.com/openstack-infra/jeepyb

Configuration
=============

projects.ini
------------
```
[projects]
gerrit-host=review.openstack.org
local-git-dir=/var/lib/git
gerrit-key=/home/gerrit2/review_site/etc/ssh_host_rsa_key
gerrit-committer=Project Creator <openstack-infra@lists.openstack.org>
acl-dir=/home/gerrit2/acls
acl-base=/home/gerrit2/acls/project.config
cache-dir=/tmp/cache
gerrit-user=gerrit2
gerrit-system-user=gerrit2
gerrit-system-group=gerrit2
```

projects.yaml
-------------
```
- project: PROJECT_NAME
  options:
   - track-upstream
   - no-gerrit
  description: This is a great project
  upstream: https://gerrit.googlesource.com/gerrit
  upstream-prefix: upstream
  acl-config: project.config
  acl-parameters:
    project: OTHER_PROJECT_NAME
```

Using
=====
`gerrit-projects --conf test_projects.ini --project_conf test_projects.yaml -v`

Packaging
=========
`python setup.py bdist_rpm`
