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

from twisted.trial import unittest
from twisted.python.reflect import namedModule
from buildbot.steps.source import darcs
from buildbot.status.results import SUCCESS, FAILURE
from buildbot.test.util import config, sourcesteps
from buildbot.test.fake.remotecommand import ExpectRemoteRef, ExpectShell, Expect
from buildbot.steps.transfer import _FileReader


class TestDarcs(sourcesteps.SourceStepMixin, config.ConfigErrorsMixin, unittest.TestCase):

    def setUp(self):
        return self.setUpSourceStep()

    def tearDown(self):
        return self.tearDownSourceStep()


    def test_mode_full_clobber(self):
        self.setupStep(
                darcs.Darcs(repourl='http://localhost/darcs',
                                    mode='full', method='clobber'))
        self.expectCommands(
            ExpectShell(workdir='wkdir',
                        command=['darcs', '--version'])
            + 0,
            Expect('stat', dict(file='wkdir/.buildbot-patched',
                                logEnviron=True))
            + 1,
            Expect('rmdir', dict(dir='wkdir',
                                 logEnviron=True))
            + 0,
            ExpectShell(workdir='.',
                        command=['darcs', 'get', '--verbose', '--lazy',
                                 '--repo-name', 'wkdir', 'http://localhost/darcs'])
            + 0,
            ExpectShell(workdir='wkdir',
                        command=['darcs', 'changes', '--max-count=1'])
            + ExpectShell.log('stdio',
                              stdout='Tue Aug 20 09:18:41 IST 2013 abc@gmail.com')
            + 0,
        )
        self.expectOutcome(result=SUCCESS, status_text=["update"])
        self.expectProperty('got_revision', 'Tue Aug 20 09:18:41 IST 2013 abc@gmail.com', 'Darcs')
        return self.runStep()


    def test_mode_full_copy(self):
        self.setupStep(
                darcs.Darcs(repourl='http://localhost/darcs',
                                    mode='full', method='copy'))
        self.expectCommands(
            ExpectShell(workdir='wkdir',
                        command=['darcs', '--version'])
            + 0,
            Expect('stat', dict(file='wkdir/.buildbot-patched',
                                logEnviron=True))
            + 1,
            Expect('rmdir', dict(dir='wkdir',
                                 logEnviron=True,
                                 timeout=1200))
            + 0,
            Expect('stat', dict(file='source/_darcs',
                                logEnviron=True))
            + 0,
            ExpectShell(workdir='source',
                        command=['darcs', 'pull', '--all', '--verbose'])
            + 0,
            Expect('cpdir', {'fromdir': 'source', 'todir': 'build',
                             'logEnviron': True, 'timeout': 1200})
            + 0,
            ExpectShell(workdir='build',
                        command=['darcs', 'changes', '--max-count=1'])
            + ExpectShell.log('stdio',
                              stdout='Tue Aug 20 09:18:41 IST 2013 abc@gmail.com')
            + 0,
        )
        self.expectOutcome(result=SUCCESS, status_text=["update"])
        self.expectProperty('got_revision', 'Tue Aug 20 09:18:41 IST 2013 abc@gmail.com', 'Darcs')
        return self.runStep()

    def test_mode_incremental(self):
        self.setupStep(
                darcs.Darcs(repourl='http://localhost/darcs',
                                    mode='incremental'))
        self.expectCommands(
            ExpectShell(workdir='wkdir',
                        command=['darcs', '--version'])
            + 0,
            Expect('stat', dict(file='wkdir/.buildbot-patched',
                                logEnviron=True))
            + 1,
            Expect('stat', dict(file='wkdir/_darcs',
                                logEnviron=True))
            + 0,
            ExpectShell(workdir='wkdir',
                        command=['darcs', 'pull', '--all', '--verbose'])
            + 0,
            ExpectShell(workdir='wkdir',
                        command=['darcs', 'changes', '--max-count=1'])
            + ExpectShell.log('stdio',
                              stdout='Tue Aug 20 09:18:41 IST 2013 abc@gmail.com')
            + 0,
        )
        self.expectOutcome(result=SUCCESS, status_text=["update"])
        self.expectProperty('got_revision', 'Tue Aug 20 09:18:41 IST 2013 abc@gmail.com', 'Darcs')
        return self.runStep()


    def test_mode_full_clobber_retry(self):
        self.setupStep(
                darcs.Darcs(repourl='http://localhost/darcs',
                                    mode='full', method='clobber', retry=(0, 2)))
        self.expectCommands(
            ExpectShell(workdir='wkdir',
                        command=['darcs', '--version'])
            + 0,
            Expect('stat', dict(file='wkdir/.buildbot-patched',
                                logEnviron=True))
            + 1,
            Expect('rmdir', dict(dir='wkdir',
                                 logEnviron=True))
            + 0,
            ExpectShell(workdir='.',
                        command=['darcs', 'get', '--verbose', '--lazy',
                                 '--repo-name', 'wkdir', 'http://localhost/darcs'])
            + 1,
            Expect('rmdir', dict(dir='wkdir',
                                 logEnviron=True))
            + 0,
            ExpectShell(workdir='.',
                        command=['darcs', 'get', '--verbose', '--lazy',
                                 '--repo-name', 'wkdir', 'http://localhost/darcs'])
            + 1,
            Expect('rmdir', dict(dir='wkdir',
                                 logEnviron=True))
            + 0,
            ExpectShell(workdir='.',
                        command=['darcs', 'get', '--verbose', '--lazy',
                                 '--repo-name', 'wkdir', 'http://localhost/darcs'])
            + 0,
            ExpectShell(workdir='wkdir',
                        command=['darcs', 'changes', '--max-count=1'])
            + ExpectShell.log('stdio',
                              stdout='Tue Aug 20 09:18:41 IST 2013 abc@gmail.com')
            + 0,
        )
        self.expectOutcome(result=SUCCESS, status_text=["update"])
        self.expectProperty('got_revision', 'Tue Aug 20 09:18:41 IST 2013 abc@gmail.com', 'Darcs')
        return self.runStep()
