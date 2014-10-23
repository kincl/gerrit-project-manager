#! /usr/bin/env python
# Copyright (C) 2011 OpenStack, LLC.
# Copyright (c) 2012 Hewlett-Packard Development Company, L.P.
# Copyright (c) 2013 Mirantis.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

# manage_projects.py reads a config file called projects.ini
# It should look like:
# [projects]
# gerrit-host=review.openstack.org
# local-git-dir=/var/lib/git
# gerrit-key=/home/gerrit2/review_site/etc/ssh_host_rsa_key
# gerrit-committer=Project Creator <openstack-infra@lists.openstack.org>
# acl-dir=/home/gerrit2/acls
# acl-base=/home/gerrit2/acls/project.config
# cache-dir=/tmp/cache
# gerrit-user=gerrit2
# gerrit-system-user=gerrit2
# gerrit-system-group=gerrit2
#
# manage_projects.py reads a project listing file called projects.yaml
# It should look like:
# - project: PROJECT_NAME
#   options:
#    - track-upstream
#    - no-gerrit
#   description: This is a great project
#   upstream: https://gerrit.googlesource.com/gerrit
#   upstream-prefix: upstream
#   acl-config: project.config
#   acl-parameters:
#     project: OTHER_PROJECT_NAME

import argparse
import ConfigParser
import yaml
import logging
import os
import sys
import re
import shlex
import subprocess
import tempfile
import time

import gerrit_projects.gerritlib as gerritlib

from jinja2 import Environment, FileSystemLoader


class ProjectsRegistry(object):
    """read config from ini or yaml file.

    It could be used as dict 'project name' -> 'project properties'.
    """
    def __init__(self, ini_file, yaml_file, single_doc=True):
        self.yaml_doc = [c for c in yaml.safe_load_all(open(yaml_file))]
        self.ini_file = ini_file
        self.single_doc = single_doc

        self.configs_list = []
        self.defaults = {}
        self._parse_file()

    def _parse_file(self):
        if self.single_doc:
            self.configs_list = self.yaml_doc[0]
        else:
            self.configs_list = self.yaml_doc[1]

        if os.path.exists(self.ini_file):
            self.defaults = ConfigParser.ConfigParser()
            self.defaults.read(self.ini_file)
        else:
            try:
                self.defaults = self.yaml_doc[0][0]
            except IndexError:
                pass

        configs = {}
        for section in self.configs_list:
            configs[section['project']] = section

        self.configs = configs

    def __getitem__(self, item):
        return self.configs[item]

    def get_project_item(self, project, item, default=None):
        if project in self.configs:
            return self.configs[project].get(item, default)
        else:
            return default

    def get(self, item, default=None):
        return self.configs.get(item, default)

    def get_defaults(self, item, default=None):
        if os.path.exists(self.ini_file):
            section = 'projects'
            if self.defaults.has_option(section, item):
                if type(default) == bool:
                    return self.defaults.getboolean(section, item)
                else:
                    return self.defaults.get(section, item)
            return default
        else:
            return self.defaults.get(item, default)


log = logging.getLogger("manage_projects")


class FetchConfigException(Exception):
    pass


class CopyACLException(Exception):
    pass


class CreateGroupException(Exception):
    pass


def run_command(cmd, status=False, env=None):
    env = env or {}
    cmd_list = shlex.split(str(cmd))
    newenv = os.environ
    newenv.update(env)
    log.debug("Executing command: %s" % " ".join(cmd_list))
    p = subprocess.Popen(cmd_list, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, env=newenv)
    (out, nothing) = p.communicate()
    log.info("Return code: %s" % p.returncode)
    log.info("Command said: %s" % out.strip())
    if status:
        return (p.returncode, out.strip())
    return out.strip()


def run_command_status(cmd, env=None):
    env = env or {}
    return run_command(cmd, True, env)


def git_command(repo_dir, sub_cmd, env=None):
    env = env or {}
    git_dir = os.path.join(repo_dir, '.git')
    cmd = "git --git-dir=%s --work-tree=%s %s" % (git_dir, repo_dir, sub_cmd)
    status, _ = run_command(cmd, True, env)
    return status


def git_command_output(repo_dir, sub_cmd, env=None):
    env = env or {}
    git_dir = os.path.join(repo_dir, '.git')
    cmd = "git --git-dir=%s --work-tree=%s %s" % (git_dir, repo_dir, sub_cmd)
    status, out = run_command(cmd, True, env)
    return (status, out)


def fetch_config(project, remote_url, repo_path, env=None):
    env = env or {}
    # Poll for refs/meta/config as gerrit may not have written it out for
    # us yet.
    for x in range(10):
        status = git_command(repo_path, "fetch %s +refs/meta/config:"
                             "refs/remotes/gerrit-meta/config" %
                             remote_url, env)
        if status == 0:
            break
        else:
            log.debug("Failed to fetch refs/meta/config for project: %s" %
                      project['name'])
            time.sleep(2)
    if status != 0:
        log.error("Failed to fetch refs/meta/config for project: %s" % project['name'])
        raise FetchConfigException()

    # Poll for project.config as gerrit may not have committed an empty
    # one yet.
    output = ""
    for x in range(10):
        status = git_command(repo_path, "remote update --prune", env)
        if status != 0:
            log.error("Failed to update remote: %s" % remote_url)
            time.sleep(2)
            continue
        else:
            status, output = git_command_output(
                repo_path, "ls-files --with-tree=remotes/gerrit-meta/config "
                "project.config", env)
        if output.strip() != "project.config" or status != 0:
            log.debug("Failed to find project.config for project: %s" %
                      project['name'])
            time.sleep(2)
        else:
            break
    if output.strip() != "project.config" or status != 0:
        log.error("Failed to find project.config for project: %s" % project['name'])
        raise FetchConfigException()

    # Because the following fails if executed more than once you should only
    # run fetch_config once in each repo.
    status = git_command(repo_path, "checkout -b config "
                         "remotes/gerrit-meta/config")
    if status != 0:
        log.error("Failed to checkout config for project: %s" % project['name'])
        raise FetchConfigException()


def copy_acl_config(project, repo_path, ACL_DIR):

    if not os.path.exists(os.path.join(ACL_DIR, project['acl_config'])):
        raise CopyACLException()

    env = Environment(loader=FileSystemLoader(ACL_DIR))
    template = env.get_template(project['acl_config'])
    (fd, tmpname) = tempfile.mkstemp(text=True)
    template.stream(project=project).dump(tmpname)
    os.close(fd)

    acl_dest = os.path.join(repo_path, "project.config")
    status, _ = run_command("cp %s %s" %
                            (tmpname, acl_dest), status=True)
    os.unlink(tmpname)

    if status != 0:
        raise CopyACLException()

    status = git_command(repo_path, "diff --quiet")
    return status != 0


def push_acl_config(project, remote_url, repo_path, gitid, env=None):
    env = env or {}
    cmd = "commit -a -m'Update project config.' --author='%s'" % gitid
    status = git_command(repo_path, cmd)
    if status != 0:
        log.error("Failed to commit config for project: %s" % project['name'])
        return False
    status, out = git_command_output(repo_path,
                                     "push %s HEAD:refs/meta/config" %
                                     remote_url, env)
    if status != 0:
        log.error("Failed to push config for project: %s" % project['name'])
        return False
    return True


def get_group_uuid(gerrit, group):
    uuid = gerrit.getGroupUUID(group)
    if uuid:
        return uuid
    gerrit.createGroup(group)
    uuid = gerrit.getGroupUUID(group)
    if uuid:
        return uuid
    return None


def create_groups_file(project, gerrit, repo_path):
    acl_config = os.path.join(repo_path, "project.config")
    group_file = os.path.join(repo_path, "groups")
    uuids = {}
    for line in open(acl_config, 'r'):
        r = re.match(r'^.*\sgroup\s+(.*)$', line)
        if r:
            group = r.group(1)
            if group in uuids.keys():
                continue
            uuid = get_group_uuid(gerrit, group)
            if uuid:
                uuids[group] = uuid
            else:
                log.error("Unable to get UUID for group %s." % group)
                raise CreateGroupException()
    if uuids:
        with open(group_file, 'w') as fp:
            for group, uuid in uuids.items():
                fp.write("%s\t%s\n" % (uuid, group))
    status = git_command(repo_path, "add groups")
    if status != 0:
        log.error("Failed to add groups file for project: %s" % project['name'])
        raise CreateGroupException()


def make_ssh_wrapper(gerrit_user, gerrit_key):
    (fd, name) = tempfile.mkstemp(text=True)
    os.write(fd, '#!/bin/bash\n')
    os.write(fd,
             'ssh -i %s -l %s -o "StrictHostKeyChecking no" $@\n' %
             (gerrit_key, gerrit_user))
    os.close(fd)
    os.chmod(name, 0o755)
    return dict(GIT_SSH=name)


# TODO(mordred): Inspect repo_dir:master for a description
#                override
def find_description_override(repo_path):
    return None


def make_local_copy(repo_path, project, project_list,
                    git_opts, ssh_env, GERRIT_HOST, GERRIT_PORT,
                    project_git, GERRIT_GITID, gerrit):

    # Ensure that the base location exists
    if not os.path.exists(os.path.dirname(repo_path)):
        os.makedirs(os.path.dirname(repo_path))

    # Three choices
    #  - If gerrit has it, get from gerrit
    #  - If gerrit doesn't have it:
    #    - If it has an upstream, clone that
    #    - If it doesn't, create it

    # Gerrit knows about the project, clone it
    # TODO(mordred): there is a possible failure condition here
    #                we should consider 'gerrit has it' to be
    #                'gerrit repo has a master branch'
    # ^DONE(kincl)
    if project['name'] in project_list and 'refs/heads/master' in gerrit.listProjectRefs(project['name']):
        run_command(
            "git clone %(remote_url)s %(repo_path)s" % git_opts,
            env=ssh_env)
        if project['upstream']:
            git_command(
                repo_path,
                "remote add -f upstream %(upstream)s" % git_opts)
        return None

    # Gerrit doesn't have it, but it has an upstream configured
    # We're probably importing it for the first time, clone
    # upstream, but then ongoing we want gerrit to ge origin
    # and upstream to be only there for ongoing tracking
    # purposes, so rename origin to upstream and add a new
    # origin remote that points at gerrit
    elif project['upstream']:
        run_command(
            "git clone %(upstream)s %(repo_path)s" % git_opts,
            env=ssh_env)
        git_command(
            repo_path,
            "fetch origin +refs/heads/*:refs/copy/heads/*",
            env=ssh_env)
        git_command(repo_path, "remote rename origin upstream")
        git_command(
            repo_path,
            "remote add origin %(remote_url)s" % git_opts)
        return "push %s +refs/copy/heads/*:refs/heads/*"

    # Neither gerrit has it, nor does it have an upstream,
    # just create a whole new one
    else:
        run_command("git init %s" % repo_path)
        git_command(
            repo_path,
            "remote add origin %(remote_url)s" % git_opts)
        with open(os.path.join(repo_path,
                               ".gitreview"),
                  'w') as gitreview:
            gitreview.write("""[gerrit]
host=%s
port=%s
project=%s
""" % (GERRIT_HOST, GERRIT_PORT, project_git))
        git_command(repo_path, "add .gitreview")
        cmd = ("commit -a -m'Added .gitreview' --author='%s'"
               % GERRIT_GITID)
        git_command(repo_path, cmd)
        return "push %s HEAD:refs/heads/master"


def update_local_copy(repo_path, track_upstream, git_opts, ssh_env):
    has_upstream_remote = (
        'upstream' in git_command_output(repo_path, 'remote')[1])
    if track_upstream:
        # If we're configured to track upstream but the repo
        # does not have an upstream remote, add one
        if not has_upstream_remote:
            git_command(
                repo_path,
                "remote add upstream %(upstream)s" % git_opts)

        # If we're configured to track upstream, make sure that
        # the upstream URL matches the config
        else:
            git_command(
                repo_path,
                "remote set-url upstream %(upstream)s" % git_opts)

        # Now that we have any upstreams configured, fetch all of the refs
        # we might need, pruning remote branches that no longer exist
        git_command(
            repo_path, "remote update --prune", env=ssh_env)
    else:
        # If we are not tracking upstream, then we do not need
        # an upstream remote configured
        if has_upstream_remote:
            git_command(repo_path, "remote rm upstream")

    # TODO(mordred): This is here so that later we can
    # inspect the master branch for meta-info
    # Checkout master and reset to the state of origin/master
    git_command(repo_path, "checkout -b master origin/master")


def push_to_gerrit(repo_path, project, push_string, remote_url, ssh_env):
    try:
        git_command(repo_path, push_string % remote_url, env=ssh_env)
        git_command(repo_path, "push --tags %s" % remote_url, env=ssh_env)
    except Exception:
        log.exception(
            "Error pushing %s to Gerrit." % project)


def sync_upstream(repo_path, project, ssh_env):
    git_command(
        repo_path,
        "remote update upstream --prune", env=ssh_env)
    # Any branch that exists in the upstream remote, we want
    # a local branch of, optionally prefixed with the
    # upstream prefix value
    for branch in git_command_output(
            repo_path, "branch -a")[1].split('\n'):
        if not branch.strip().startswith("remotes/upstream"):
            continue
        if "->" in branch:
            continue
        local_branch = branch.split()[0][len('remotes/upstream/'):]
        if project['upstream_prefix']:
            local_branch = "%s/%s" % (
                project['upstream_prefix'], local_branch)

        # Check out an up to date copy of the branch, so that
        # we can push it and it will get picked up below
        git_command(repo_path, "checkout -b %s %s" % (
            local_branch, branch))

    try:
        # Push all of the local branches to similarly named
        # Branches on gerrit. Also, push all of the tags
        git_command(
            repo_path,
            "push origin refs/heads/*:refs/heads/*",
            env=ssh_env)
        git_command(repo_path, 'push origin --tags', env=ssh_env)
    except Exception:
        log.exception(
            "Error pushing %s to Gerrit." % project['name'])


def process_acls(project, ACL_DIR, remote_url, repo_path,
                 ssh_env, gerrit, GERRIT_GITID):
    if not os.path.isfile(os.path.join(ACL_DIR, project['acl_config'])):
        return
    try:
        fetch_config(project, remote_url, repo_path, ssh_env)
        if not copy_acl_config(project, repo_path, ACL_DIR):
            # nothing was copied, so we're done
            return
        create_groups_file(project, gerrit, repo_path)
        push_acl_config(project, remote_url, repo_path,
                        GERRIT_GITID, ssh_env)
    except Exception:
        log.exception(
            "Exception processing ACLS for %s." % project['name'])
    finally:
        git_command(repo_path, 'reset --hard')
        git_command(repo_path, 'checkout master')
        git_command(repo_path, 'branch -D config')


def create_gerrit_project(project, project_list, gerrit):
    if project not in project_list:
        try:
            gerrit.createProject(project)
            return True
        except Exception:
            log.exception(
                "Exception creating %s in Gerrit." % project)
            raise
    return False


def create_local_mirror(local_git_dir, project_git,
                        gerrit_system_user, gerrit_system_group):

    git_mirror_path = os.path.join(local_git_dir, project_git)
    if not os.path.exists(git_mirror_path):
        (ret, output) = run_command_status(
            "git init --bare %s" % git_mirror_path)
        if ret:
            run_command("rm -rf git_mirror_path")
            raise Exception(output)
        run_command("chown -R %s:%s %s"
                    % (gerrit_system_user, gerrit_system_group,
                       git_mirror_path))


def main():
    parser = argparse.ArgumentParser(description='Manage projects')
    parser.add_argument('-v', dest='verbose', action='store_true',
                        help='verbose output')
    parser.add_argument('-d', dest='debug', action='store_true',
                        help='debug output')
    parser.add_argument('--conf', dest='conf', help='Configuration file',
                        default='/home/gerrit2/projects.ini')
    parser.add_argument('--project_conf', dest='project_conf',
                        help='Project YAML configuration file',
                        default='/home/gerrit2/projects.yaml')
    #parser.add_argument('--nocleanup', action='store_true',
    #                    help='do not remove temp directories')
    parser.add_argument('projects', metavar='project', nargs='*',
                        help='name of project(s) to process')
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG,
                            format='%(asctime)-6s: %(name)s - %(levelname)s'
                                   ' - %(message)s')
    elif args.verbose:
        logging.basicConfig(level=logging.INFO,
                            format='%(asctime)-6s: %(name)s - %(levelname)s'
                                   ' - %(message)s')
    else:
        logging.basicConfig(level=logging.ERROR,
                            format='%(asctime)-6s: %(name)s - %(levelname)s'
                                   ' - %(message)s')

    for f in [args.conf, args.project_conf]:
        if not os.path.exists(f):
            logging.error('File must exist! %s' % f)
            sys.exit(1)
    registry = ProjectsRegistry(args.conf, args.project_conf)

    LOCAL_GIT_DIR = registry.get_defaults('local-git-dir', '/var/lib/git')
    CACHE_DIR = registry.get_defaults('cache-dir',
                                      '/var/tmp/cache')
    ACL_DIR = registry.get_defaults('acl-dir')
    GERRIT_HOST = registry.get_defaults('gerrit-host')
    GERRIT_PORT = int(registry.get_defaults('gerrit-port', '29418'))
    GERRIT_USER = registry.get_defaults('gerrit-user')
    GERRIT_KEY = registry.get_defaults('gerrit-key')
    GERRIT_GITID = registry.get_defaults('gerrit-committer')
    GERRIT_SYSTEM_USER = registry.get_defaults('gerrit-system-user', 'gerrit2')
    GERRIT_SYSTEM_GROUP = registry.get_defaults('gerrit-system-group',
                                                'gerrit2')

    gerrit = gerritlib.Gerrit(GERRIT_HOST,
                              GERRIT_USER,
                              GERRIT_PORT,
                              GERRIT_KEY)
    project_list = gerrit.listProjects()
    ssh_env = make_ssh_wrapper(GERRIT_USER, GERRIT_KEY)

    try:

        for section in registry.configs_list:
            project = dict(name=section['project'])
            if args.projects and project not in args.projects:
                continue

            try:
                # Figure out all of the options
                project['options']= section.get('options', dict())
                project['description'] = section.get('description', None)
                project['upstream'] = section.get('upstream', None)
                project['upstream_prefix'] = section.get('upstream-prefix', None)
                project['track_upstream'] = 'track-upstream' in project['options']
                project['acl_config'] = section.get('acl-config',
                                                    '%s.config' % project['name'])
                repo_path = os.path.join(CACHE_DIR, project['name'])

                # If this project doesn't want to use gerrit, exit cleanly.
                if 'no-gerrit' in project['options']:
                    continue

                project_git = "%s.git" % project['name']
                remote_url = "ssh://%s:%s/%s" % (
                    GERRIT_HOST,
                    GERRIT_PORT,
                    project['name'])
                git_opts = dict(upstream=project['upstream'],
                                repo_path=repo_path,
                                remote_url=remote_url)

                # Create the project in Gerrit first, since it will fail
                # spectacularly if its project directory or local replica
                # already exist on disk
                project_created = create_gerrit_project(
                    project['name'], project_list, gerrit)

                # Create the repo for the local git mirror
                create_local_mirror(
                    LOCAL_GIT_DIR, project_git,
                    GERRIT_SYSTEM_USER, GERRIT_SYSTEM_GROUP)

                if not os.path.exists(repo_path) or project_created:
                    # We don't have a local copy already, get one

                    # Make Local repo
                    push_string = make_local_copy(
                        repo_path, project, project_list,
                        git_opts, ssh_env, GERRIT_HOST, GERRIT_PORT,
                        project_git, GERRIT_GITID, gerrit)
                else:
                    # We do have a local copy of it already, make sure it's
                    # in shape to have work done.
                    update_local_copy(
                        repo_path, project['track_upstream'], git_opts, ssh_env)

                #description = (
                #    find_description_override(repo_path) or description)

                if project_created:
                    push_to_gerrit(
                        repo_path, project['name'], push_string, remote_url, ssh_env)
                    gerrit.replicate(project['name'])

                # If we're configured to track upstream, make sure we have
                # upstream's refs, and then push them to the appropriate
                # branches in gerrit
                if project['track_upstream']:
                    sync_upstream(repo_path, project, ssh_env)

                if project['acl_config'] and os.path.exists(project['acl_config']):
                    process_acls(
                        project, ACL_DIR, remote_url, repo_path,
                        ssh_env, gerrit, GERRIT_GITID)
                else:
                    if project['description']:
                        gerrit.updateProject(project['name'], 'description', project['description'])

            except Exception:
                log.exception(
                    "Problems creating %s, moving on." % project['name'])
                continue
    finally:
        os.unlink(ssh_env['GIT_SSH'])

if __name__ == "__main__":
    main()
