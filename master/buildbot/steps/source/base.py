# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members


import os

from twisted.python import log
from twisted.internet import defer
from buildbot.process.buildstep import LoggingBuildStep
from buildbot.status.builder import SKIPPED, FAILURE
from buildbot.steps.slave import CompositeStepMixin
from buildbot.steps.transfer import _FileReader
from buildbot.process import buildstep

class Source(LoggingBuildStep, CompositeStepMixin):
    """This is a base class to generate a source tree in the buildslave.
    Each version control system has a specialized subclass, and is expected
    to override __init__ and implement computeSourceRevision() and
    startVC(). The class as a whole builds up the self.args dictionary, then
    starts a RemoteCommand with those arguments.
    """

    renderables = LoggingBuildStep.renderables + [
                     'description', 'descriptionDone', 'descriptionSuffix',
                     'workdir' ]

    description = None # set this to a list of short strings to override
    descriptionDone = None # alternate description when the step is complete
    descriptionSuffix = None # extra information to append to suffix

    # if the checkout fails, there's no point in doing anything else
    haltOnFailure = True
    flunkOnFailure = True
    notReally = False

    branch = None # the default branch, should be set in __init__

    def __init__(self, workdir=None, mode='update', alwaysUseLatest=False,
                 timeout=20*60, retry=None, env=None, logEnviron=True,
                 description=None, descriptionDone=None, descriptionSuffix=None,
                 codebase='', **kwargs):
        """
        @type  workdir: string
        @param workdir: local directory (relative to the Builder's root)
                        where the tree should be placed

        @type  alwaysUseLatest: boolean
        @param alwaysUseLatest: whether to always update to the most
        recent available sources for this build.

        Normally the Source step asks its Build for a list of all
        Changes that are supposed to go into the build, then computes a
        'source stamp' (revision number or timestamp) that will cause
        exactly that set of changes to be present in the checked out
        tree. This is turned into, e.g., 'cvs update -D timestamp', or
        'svn update -r revnum'. If alwaysUseLatest=True, bypass this
        computation and always update to the latest available sources
        for each build.

        The source stamp helps avoid a race condition in which someone
        commits a change after the master has decided to start a build
        but before the slave finishes checking out the sources. At best
        this results in a build which contains more changes than the
        buildmaster thinks it has (possibly resulting in the wrong
        person taking the blame for any problems that result), at worst
        is can result in an incoherent set of sources (splitting a
        non-atomic commit) which may not build at all.

        @type logEnviron: boolean
        @param logEnviron: If this option is true (the default), then the
                           step's logfile will describe the environment
                           variables on the slave. In situations where the
                           environment is not relevant and is long, it may
                           be easier to set logEnviron=False.

        @type codebase: string
        @param codebase: Specifies which changes in a build are processed by
        the step. The default codebase value is ''. The codebase must correspond
        to a codebase assigned by the codebaseGenerator. If no codebaseGenerator
        is defined in the master then codebase doesn't need to be set, the
        default value will then match all changes.
        """

        LoggingBuildStep.__init__(self, **kwargs)

        # This will get added to args later, after properties are rendered
        self.workdir = workdir

        self.sourcestamp = None

        self.codebase = codebase
        if self.codebase:
            self.name = ' '.join((self.name, self.codebase))

        self.alwaysUseLatest = alwaysUseLatest

        self.logEnviron = logEnviron
        self.env = env
        self.timeout = timeout

        descriptions_for_mode = {
            "clobber": "checkout",
            "export": "exporting"}
        descriptionDones_for_mode = {
            "clobber": "checkout",
            "export": "export"}
        if description:
            self.description = description
        else:
            self.description = [
                descriptions_for_mode.get(mode, "updating")]
        if isinstance(self.description, str):
            self.description = [self.description]

        if descriptionDone:
            self.descriptionDone = descriptionDone
        else:
            self.descriptionDone = [
                descriptionDones_for_mode.get(mode, "update")]
        if isinstance(self.descriptionDone, str):
            self.descriptionDone = [self.descriptionDone]

        if descriptionSuffix:
            self.descriptionSuffix = descriptionSuffix
        else:
            self.descriptionSuffix = self.codebase or None # want None in lieu of ''
        if isinstance(self.descriptionSuffix, str):
            self.descriptionSuffix = [self.descriptionSuffix]

    def updateSourceProperty(self, name, value, source=''):
        """
        Update a property, indexing the property by codebase if codebase is not
        ''.  Source steps should generally use this instead of setProperty.
        """
        # pick a decent source name
        if source == '':
            source = self.__class__.__name__

        if self.codebase != '':
            assert not isinstance(self.getProperty(name, None), str), \
             "Sourcestep %s has a codebase, other sourcesteps don't" \
             % self.name
            property_dict = self.getProperty(name, {})
            property_dict[self.codebase] = value
            LoggingBuildStep.setProperty(self, name, property_dict, source)
        else:
            assert not isinstance(self.getProperty(name, None), dict), \
             "Sourcestep %s does not have a codebase, other sourcesteps do" \
             % self.name
            LoggingBuildStep.setProperty(self, name, value, source)

    def setStepStatus(self, step_status):
        LoggingBuildStep.setStepStatus(self, step_status)

    def setDefaultWorkdir(self, workdir):
        self.workdir = self.workdir or workdir

    def describe(self, done=False):
        desc = self.descriptionDone if done else self.description
        if self.descriptionSuffix:
            desc = desc[:]
            desc.extend(self.descriptionSuffix)
        return desc

    def computeSourceRevision(self, changes):
        """Each subclass must implement this method to do something more
        precise than -rHEAD every time. For version control systems that use
        repository-wide change numbers (SVN, P4), this can simply take the
        maximum such number from all the changes involved in this build. For
        systems that do not (CVS), it needs to create a timestamp based upon
        the latest Change, the Build's treeStableTimer, and an optional
        self.checkoutDelay value."""
        return None


    def _doRemoteShellCommand(self, _, command):
        cmd = buildstep.RemoteShellCommand(self.workdir,
                                           cmd,
                                           env=self.env,
                                           logEnviron=self.logEnviron,
                                           timeout=self.timeout,
                                           collectStdout=collectStdout)

        cmd.userLog(self.stdio_log, False)
        d = self.runCommand(cmd)
        def evaluateCommand(cmd):
            if cmd.didFail():
                log.msg('%s failed to execute' % cmd)
                raise buildstep.BuildStepFailed()
            if collectStdout:
                return cmd.stdout
            else:
                return cmd.rc
        d.addCallback(lambda _: evaluateCommand(cmd))
        return d

    def _downloadFile(self, filename, slavedest):
        try:
            fp = open(filename, 'rb')
        except IOError:
            self.addCompleteLog('stderr',
                                'File %r not available at master' % filename)
            eventually(BuildStep.finished, self, FAILURE)
            return

        fileReader = _FileReader(fp)
        args = {
            'slavedest': slavedest,
            'maxsize': None,
            'reader': fileReader,
            'blocksize': 16*1024,
            'workdir': self.workdir,
            }
        cmd = buildstep.RemoteCommand('downloadFile', args)
        d = self.runCommand(cmd)
        def evaluateCommand(cmd):
            if cmd.didFail():
                raise buildstep.BuildStepFailed()
                return cmd.rc
        d.addCallback(lambda _: evaluateCommand(cmd))

        return d


    def patch(self, _, patch):
        patchlevel = patch[0]
        diff = patch[1]
        root = None
        if len(patch) >= 3:
            root = patch[2]
        # TODO: update work directory if root is not None

        open(".buildbot-diff", "w").write(diff)
        open(".buildbot-patched", "w").write("patched\n")

        d = self._downloadFile(".buildbot-diff", self.workdir)

        d.addCallback(lambda _ : self._downloadFile(".buildbot-patched", self.workdir))
        os.unlink(".buildbot-diff")
        os.unlink(".buildbot-patched")

        patch_command = ['patch', '-p%s' % patchlevel, '--remove-empty-files',
                         '--force', '--forward', '-i', '.buildbot-diff']
        d.addCallback(self._doRemoteShellCommand(patch_command))
        # TODO: remove remote .bulidbot-diff file
        return d
                                           
    def start(self):
        if self.notReally:
            log.msg("faking %s checkout/update" % self.name)
            self.step_status.setText(["fake", self.name, "successful"])
            self.addCompleteLog("log",
                                "Faked %s checkout/update 'successful'\n" \
                                % self.name)
            return SKIPPED

        if not self.alwaysUseLatest:
            # what source stamp would this step like to use?
            s = self.build.getSourceStamp(self.codebase)
            self.sourcestamp = s

            if self.sourcestamp:
                # if branch is None, then use the Step's "default" branch
                branch = s.branch or self.branch
                # if revision is None, use the latest sources (-rHEAD)
                revision = s.revision
                if not revision:
                    revision = self.computeSourceRevision(s.changes)
                    # the revision property is currently None, so set it to something
                    # more interesting
                    if revision is not None:
                        self.updateSourceProperty('revision', str(revision))

                # if patch is None, then do not patch the tree after checkout

                # 'patch' is None or a tuple of (patchlevel, diff, root)
                # root is optional.
                patch = s.patch
                if patch:
                    self.addCompleteLog("patch", patch[1])
            else:
                log.msg("No sourcestamp found in build for codebase '%s'" % self.codebase)
                self.step_status.setText(["Codebase", '%s' % self.codebase ,"not", "in", "build" ])
                self.addCompleteLog("log",
                                    "No sourcestamp found in build for codebase '%s'" \
                                    % self.codebase)
                self.finished(FAILURE)
                return FAILURE

        else:
            revision = None
            branch = self.branch
            patch = None

        self.startVC(branch, revision, patch)

