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

## Source step code for Monotone

import os

from twisted.python import log
from twisted.internet import defer, reactor

from buildbot.process import buildstep
from buildbot.steps.source.base import Source
from buildbot.interfaces import BuildSlaveTooOldError
from buildbot.config import ConfigErrors
from buildbot.status.results import SUCCESS

class Monotone(Source):
    """ Class for Monotone with all smarts """

    name = 'monotone'

    renderables = ['repourl']
    possible_modes = ('incremental', 'full')
    possible_methods = ('clobber', 'copy', 'full', 'clean')

    def __init__(self, repourl=None, branch=None, database=None, mode='incremental',
                 method=None, **kwargs):

        self.repourl = repourl
        self.method = method
        self.mode = mode
        self.branch = branch
        self.sourcedata = "%s?%s" % (self.repourl, self.branch)
        self.database = '../db.mtn'
        Source.__init__(self, **kwargs)
        errors = []

        if self.mode not in self.possible_modes:
            errors.append("mode %s is not one of %s" % (self.mode, self.possible_modes))
        if self.mode == 'incremental' and self.method:
            errors.append("Incremental mode does not require method")
        
        if self.mode == 'full':
            if self.method == None:
                self.method = 'copy'
            elif self.method not in self.possible_methods:
                errors.append("Invalid method for mode == %s" % (self.mode))

        if repourl is None:
            errors.append("you must provide repourl")

        if branch is None:
            errors.append("you must provide branch")

        if errors:
            raise ConfigErrors(errors)

    def startVC(self, branch, revision, patch):
        self.revision = revision
        self.stdio_log = self.addLogForRemoteCommands("stdio")

        d = self.checkMonotone()
        def checkInstall(monotoneInstalled):
            if not monotoneInstalled:
                raise BuildSlaveTooOldError("Monotone is not installed on slave")
            return 0
        d.addCallback(checkInstall)
        d.addCallback(lambda _: self.sourcedirIsPatched())
        def checkPatched(patched):
            if patched:
                return self.cleanWorkdir()
            else:
                return 0
        d.addCallback(checkPatched)
        if self.mode == 'full':
            d.addCallback(lambda _: self.full())
        elif self.mode == 'incremental':
            d.addCallback(lambda _: self.incremental())

        if patch:
            d.addCallback(self.patch, patch)
        d.addCallback(self.parseGotRevision)
        d.addCallback(self.finish)
        d.addErrback(self.failed)
        return d

    @defer.inlineCallbacks
    def full(self):
        if self.method == 'clobber':
            yield self.clobber()
            return
        elif self.method == 'copy':
            yield self.copy()
            return

        updatable = yield self._sourcedirIsUpdatable()
        if not updatable:
            yield self._retryClone()
        elif self.method == 'clean':
            yield self.clean()
        elif self.method == 'fresh':
            yield self.clean(False)
        else:
            raise ValueError("Unknown method, check your configuration")

    @defer.inlineCallbacks
    def incremental(self):
        updatable = yield self._sourcedirIsUpdatable()
        if not updatable:
            yield self._retryClone()
        else:
            yield self._pull()
            command = ['mtn', 'update',
                       '--db=%s' % (self.database)]
            if self.revision:
                command.extend(['--revision', self.revision])
            else:
                command.extend(["-r", "h:" + self.branch])
            command.extend(["-b", self.branch])

            yield self._dovccmd(command)

    def clobber(self):
        d = self.runRmdir(self.workdir)
        d.addCallback(lambda _: self._retryClone())
        return d

    def copy(self):
        cmd = buildstep.RemoteCommand('rmdir', {'dir': self.workdir,
                                                'logEnviron': self.logEnviron,
                                                'timeout': self.timeout,})
        cmd.useLog(self.stdio_log, False)
        d = self.runCommand(cmd)

        self.workdir = 'source'
        d.addCallback(lambda _: self.incremental())
        def copy(_):
            cmd = buildstep.RemoteCommand('cpdir',
                                          {'fromdir': 'source',
                                           'todir':'build',
                                           'logEnviron': self.logEnviron,
                                           'timeout': self.timeout,})
            cmd.useLog(self.stdio_log, False)
            d = self.runCommand(cmd)
            return d
        d.addCallback(copy)
        def resetWorkdir(_):
            self.workdir = 'build'
            return 0

        d.addCallback(resetWorkdir)
        return d

    def checkMonotone(self):
        cmd = buildstep.RemoteShellCommand(self.workdir, ['mtn', '--version'],
                                           env=self.env,
                                           logEnviron=self.logEnviron,
                                           timeout=self.timeout)
        cmd.useLog(self.stdio_log, False)
        d = self.runCommand(cmd)
        def evaluate(cmd):
            if cmd.rc != 0:
                return False
            return True
        d.addCallback(lambda _: evaluate(cmd))
        return d

    @defer.inlineCallbacks
    def clean(self, ignore_ignored=True):
        files = []
        commands = [['mtn', 'ls', 'unknown']]
        if not ignore_ignored:
            commands.append(['mtn', 'ls', 'ignored'])
        for cmd in commands:
            stdout = yield self._dovccmd(cmd, collectStdout=True)
            for filename in stdout.strip().split('\n'):
                filename = self.workdir+'/'+str(filename)
                files.append(filename)

        print files
        if len(files) == 0:
            rc = 0
        else:
            if self.slaveVersionIsOlderThan('rmdir', '2.14'):
                rc = yield self.removeFiles(files)
            else:
                rc = yield self.runRmdir(files, abandonOnFailure=False)

        if rc != 0:
            log.msg("Failed removing files")
            raise buildstep.BuildStepFailed()

    @defer.inlineCallbacks
    def removeFiles(self, files):
        for filename in files:
            res = yield self.runRmdir(filename, abandonOnFailure=False)
            if res:
                defer.returnValue(res)
                return
        defer.returnValue(0)

    def _clone(self, abandonOnFailure=False):
        command = ['mtn', '--db=%s' % (self.database), 'clone', '.', self.sourcedata]
        # if self.revision:
        #     command.extend(['--revision', self.revision])
        command.extend(['--branch', self.branch])

        d = self._dovccmd(command, abandonOnFailure=abandonOnFailure)
        return d

    def _checkout(self, abandonOnFailure=False):
        d = self._pull()
        command = ['mtn', 'checkout', '.', '--db=%s' % (self.database)]
        if self.revision:
            command.extend(['--revision', self.revision])
        command.extend(['--branch', self.branch])

        d.addCallback(lambda _: self._dovccmd(command, abandonOnFailure=abandonOnFailure))
        return d

    def _pull(self):
        command = ['mtn', 'pull', self.sourcedata,
                   '--db=%s' % (self.database)]
        if self.progress:
            command.extend(['--ticker=dot'])
        else:
            command.extend(['--ticker=none'])
        d = self._dovccmd(command)
        return d

    def _retryClone(self):
        if self.retry:
            abandonOnFailure = (self.retry[1] <= 0)
        else:
            abandonOnFailure = True

        d = self._clone(abandonOnFailure)
        def _retry(res):
            if self.stopped or res == 0:
                return res
            delay, repeats = self.retry
            if repeats > 0:
                log.msg("Checkout failed, trying %d more times after %d seconds" 
                    % (repeats, delay))
                self.retry = (delay, repeats-1)
                df = defer.Deferred()
                df.addCallback(lambda _: self.runRmdir(self.workdir))
                df.addCallback(lambda _: self._retryClone())
                reactor.callLater(delay, df.callback, None)
                return df
            return res

        if self.retry:
            d.addCallback(_retry)
        return d

    @defer.inlineCallbacks
    def parseGotRevision(self, _=None):
        stdout = yield self._dovccmd(['mtn', 'automate', 'select', 'w:'], collectStdout=True)
        revision = stdout.strip()
        if len(revision) != 40:
            raise buildstep.BuildStepFailed()
        log.msg("Got Monotone revision %s" % (revision, ))
        self.updateSourceProperty('got_revision', revision)
        defer.returnValue(0)

    def _dovccmd(self, command, collectStdout=False, initialStdin=None, decodeRC={0:SUCCESS},
                 abandonOnFailure=True, wkdir=None):
        if not command:
            raise ValueError("No command specified")
        workdir = wkdir or self.workdir
        cmd = buildstep.RemoteShellCommand(workdir, command,
                                           env=self.env,
                                           logEnviron=self.logEnviron,
                                           timeout=self.timeout,
                                           collectStdout=collectStdout,
                                           initialStdin=initialStdin,
                                           decodeRC=decodeRC)
        cmd.useLog(self.stdio_log, False)
        d = self.runCommand(cmd)
        def evaluateCommand(cmd):
            if abandonOnFailure and cmd.didFail():
                log.msg("Source step failed while running command %s" % cmd)
                raise buildstep.BuildStepFailed()
            if collectStdout:
                return cmd.stdout
            else:
                return cmd.rc
        d.addCallback(lambda _: evaluateCommand(cmd))
        return d

    def _sourcedirIsUpdatable(self):
        return self.pathExists(self.build.path_module.join(self.workdir, '_MTN'))

    def finish(self, res):
        d = defer.succeed(res)
        def _gotResults(results):
            self.setStatus(self.cmd, results)
            log.msg("Closing log, sending result of the command %s " % \
                        (self.cmd))
            return results
        d.addCallback(_gotResults)
        d.addCallbacks(self.finished, self.checkDisconnect)
        return d

