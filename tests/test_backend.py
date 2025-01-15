#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Copyright (C) 2015-2020 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# Authors:
#     Valerio Cosentino <valcos@bitergia.com>
#     Santiago Dueñas <sduenas@bitergia.com>
#     Jesus M. Gonzalez-Barahona <jgb@gsyc.es>
#     Miguel Ángel Fernández <mafesan@bitergia.com>
#     Harshal Mittal <harshalmittal4@gmail.com>
#     JJMerchante <jj.merchante@gmail.com>
#

import argparse
import datetime
import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import unittest.mock

import dateutil.tz

from grimoirelab_toolkit.datetime import (InvalidDateError,
                                          datetime_utcnow,
                                          str_to_datetime)
from perceval.backends.core import __version__
from perceval.archive import Archive, ArchiveManager
from perceval.backend import (Backend,
                              BackendCommandArgumentParser,
                              BackendCommand,
                              BackendItemsGenerator,
                              OriginUniqueField,
                              Summary,
                              uuid,
                              fetch,
                              fetch_from_archive,
                              find_backends,
                              logger as backend_logger)
from perceval.errors import ArchiveError, BackendError, BackendCommandArgumentParserError
from perceval.utils import DEFAULT_DATETIME
from base import TestCaseBackendArchive
import mocked_package
from mocked_package.backend import (BackendA,
                                    BackendCommandA)
import mocked_package.nested_package
from mocked_package.nested_package.nested_backend_b import (BackendB,
                                                            BackendCommandB)
from mocked_package.nested_package.nested_backend_c import (BackendC,
                                                            BackendCommandC)


SUMMARY_LOG_REPORT = """INFO:perceval.backend:Summary of results

\t   Total items: \t5
\tItems produced: \t5
\t Items skipped: \t0

\tLast item UUID: \t6130c145435d661565bd7d402be403bea7cfb6b5
\tLast item date: \t2016-01-01 00:00:04+00:00

\tMin. item date: \t2016-01-01 00:00:00+00:00
\tMax. item date: \t2016-01-01 00:00:04+00:00

\tMin. offset: \t-\tMax. offset: \t-\tLast offset: \t-

"""


class MockedBackend(Backend):
    """Mocked backend for testing"""

    version = '0.2.0'
    DEFAULT_CATEGORY = "mock_item"
    OTHER_CATEGORY = "alt_item"
    CATEGORIES = [DEFAULT_CATEGORY, OTHER_CATEGORY]
    SEARCH_FIELDS = {}
    ITEMS = 5

    def __init__(self, origin, tag=None, archive=None, blacklist_ids=None):
        super().__init__(origin, tag=tag, archive=archive, blacklist_ids=blacklist_ids)
        self._fetch_from_archive = False

    def fetch(self, category=DEFAULT_CATEGORY, filter_classified=False):
        return super().fetch(category, filter_classified=filter_classified)

    def fetch_items(self, category, **kwargs):
        for x in range(MockedBackend.ITEMS):
            if self._fetch_from_archive:
                item = self.archive.retrieve(str(x), None, None)
                yield item
            else:
                item = {'item': x, 'category': category}
                if self.archive:
                    self.archive.store(str(x), None, None, item)
                yield item

    def _init_client(self, from_archive=False):
        self._fetch_from_archive = from_archive
        return None

    @staticmethod
    def metadata_id(item):
        return str(item['item'])

    @staticmethod
    def metadata_updated_on(item):
        return str_to_datetime('2016-01-01').timestamp() + item['item']

    @staticmethod
    def metadata_category(item):
        return item['category']


class MockedBackendBlacklist(MockedBackend):
    """Mocked backend for testing blacklist items filtering"""

    ORIGIN_UNIQUE_FIELD = OriginUniqueField(name='item', type=int)
    DEFAULT_CATEGORY = "mock_item"

    def __init__(self, origin, tag=None, archive=None, blacklist_ids=None):
        super().__init__(origin, tag=tag, archive=archive, blacklist_ids=blacklist_ids)
        self._fetch_from_archive = False

    def fetch(self, category=DEFAULT_CATEGORY, filter_classified=False):
        return super().fetch(category, filter_classified=filter_classified)

    def fetch_items(self, category, **kwargs):
        for x in range(MockedBackend.ITEMS):
            if self._fetch_from_archive:
                item = self.archive.retrieve(str(x), None, None)
            else:
                item = {'item': x, 'category': category}
                if self.archive:
                    self.archive.store(str(x), None, None, item)

            if self._skip_item(item):
                continue

            yield item


class MockedBackendBlacklistNoOriginUniqueField(MockedBackend):
    """Mocked backend for testing blacklist items filtering"""

    DEFAULT_CATEGORY = "mock_item"

    def __init__(self, origin, tag=None, archive=None, blacklist_ids=None):
        super().__init__(origin, tag=tag, archive=archive, blacklist_ids=blacklist_ids)
        self._fetch_from_archive = False

    def fetch(self, category=DEFAULT_CATEGORY, filter_classified=False):
        return super().fetch(category, filter_classified=filter_classified)

    def fetch_items(self, category, **kwargs):
        for x in range(MockedBackend.ITEMS):
            if self._fetch_from_archive:
                item = self.archive.retrieve(str(x), None, None)
            else:
                item = {'item': x, 'category': category}
                if self.archive:
                    self.archive.store(str(x), None, None, item)

            if self._skip_item(item):
                continue

            yield item


class ClassifiedFieldsBackend(MockedBackend):
    """Mocked backend for testing classified fields filtering"""

    CLASSIFIED_FIELDS = [
        ['my', 'list_classified', 'dict_classified', 'field'],
        ['my', 'classified', 'field'],
        ['classified']
    ]

    def fetch(self, category, filter_classified=False):
        return super().fetch(category, filter_classified=filter_classified)

    def fetch_items(self, category, **kwargs):
        i = 0
        for item in super().fetch_items(category, **kwargs):
            item['my'] = {
                'classified': {'field': i},
                'field': i,
                'list_classified': [{'field': i, 'dict_classified': {'field': i}}]
            }
            item['classified'] = i
            i += 1
            yield item


class NotFoundClassifiedFieldBackend(MockedBackend):
    """Mocked backend for testing classified fields not found while filtering"""

    CLASSIFIED_FIELDS = [
        ['classified_field']
    ]

    def fetch(self, category, filter_classified=False):
        return super().fetch(category, filter_classified=filter_classified)


class CommandBackend(MockedBackend):
    """Backend used for testing in BackendCommand tests"""

    def fetch_items(self, category, **kwargs):
        for item in super().fetch_items(category, **kwargs):
            if self._fetch_from_archive:
                item['archive'] = True
            yield item


class ErrorCommandBackend(CommandBackend):
    """Backend which raises an exception while fetching items"""

    def fetch_items(self, category, **kwargs):
        for item in super().fetch_items(category, **kwargs):
            yield item
            raise BackendError(cause="Unhandled exception")


class MockedBackendCommand(BackendCommand):
    """Mocked backend command class used for testing"""

    BACKEND = CommandBackend

    def __init__(self, *args):
        super().__init__(*args)

    def _pre_init(self):
        setattr(self.parsed_args, 'pre_init', True)

    def _post_init(self):
        setattr(self.parsed_args, 'post_init', True)

    @classmethod
    def setup_cmd_parser(cls):
        parser = BackendCommandArgumentParser(cls.BACKEND,
                                              from_date=True,
                                              basic_auth=True,
                                              token_auth=True,
                                              archive=True,
                                              ssl_verify=True)
        parser.parser.add_argument('origin')
        parser.parser.add_argument('--subtype', dest='subtype')

        return parser


class MockedBackendBlacklistCommand(BackendCommand):
    """Mocked backend command class used for testing"""

    BACKEND = MockedBackendBlacklist

    def __init__(self, *args):
        super().__init__(*args)

    def _pre_init(self):
        setattr(self.parsed_args, 'pre_init', True)

    def _post_init(self):
        setattr(self.parsed_args, 'post_init', True)

    @classmethod
    def setup_cmd_parser(cls):
        parser = BackendCommandArgumentParser(cls.BACKEND,
                                              blacklist=True)
        parser.parser.add_argument('origin')

        return parser


class MockedBackendBlacklistCommandNoOriginUniqueField(BackendCommand):
    """Mocked backend command class used for testing"""

    BACKEND = MockedBackendBlacklistNoOriginUniqueField

    def __init__(self, *args):
        super().__init__(*args)

    def _pre_init(self):
        setattr(self.parsed_args, 'pre_init', True)

    def _post_init(self):
        setattr(self.parsed_args, 'post_init', True)

    @classmethod
    def setup_cmd_parser(cls):
        parser = BackendCommandArgumentParser(cls.BACKEND,
                                              blacklist=True)
        parser.parser.add_argument('origin')

        return parser


class MockedBackendCommandDefaultPrePostInit(BackendCommand):
    """Mocked backend command class used for testing"""

    BACKEND = CommandBackend

    def __init__(self, *args):
        super().__init__(*args)

    @classmethod
    def setup_cmd_parser(cls):
        parser = BackendCommandArgumentParser(cls.BACKEND,
                                              from_date=True,
                                              basic_auth=True,
                                              token_auth=True,
                                              archive=True,
                                              ssl_verify=True)
        parser.parser.add_argument('origin')

        return parser


class ClassifiedFieldsBackendCommand(MockedBackendCommand):
    """Mocked backend command for testing classified fields filtering"""

    BACKEND = ClassifiedFieldsBackend


class NoArchiveBackendCommand(BackendCommand):
    """Mocked backend command class used for testing which does not support archive"""

    BACKEND = CommandBackend

    def __init__(self, *args):
        super().__init__(*args)

    def _pre_init(self):
        setattr(self.parsed_args, 'pre_init', True)

    def _post_init(self):
        setattr(self.parsed_args, 'post_init', True)

    @classmethod
    def setup_cmd_parser(cls):
        parser = BackendCommandArgumentParser(cls.BACKEND,
                                              from_date=True,
                                              archive=False)
        parser.parser.add_argument('origin')
        parser.parser.add_argument('--subtype', dest='subtype')

        return parser


class TestBackend(unittest.TestCase):
    """Unit tests for Backend"""

    def setUp(self):
        self.test_path = tempfile.mkdtemp(prefix='perceval_')

    def tearDown(self):
        shutil.rmtree(self.test_path)

    def test_version(self):
        """Test whether the backend version is initialized"""

        b = MockedBackend('test')
        self.assertEqual(b.version, MockedBackend.version)

    def test_origin(self):
        """Test whether origin value is initialized"""

        b = Backend('test')
        self.assertEqual(b.origin, 'test')

    def test_ssl_verify(self):
        """Test whether the SSL verify value is initialized"""

        b = Backend('test')
        self.assertTrue(b.ssl_verify)

    def test_classified_fields(self):
        """Test whether classified fields property returns a list with fields"""

        b = Backend('test')
        self.assertListEqual(b.classified_fields, [])

        b = ClassifiedFieldsBackend('test')
        self.assertListEqual(b.classified_fields, ['my.list_classified.dict_classified.field',
                                                   'my.classified.field',
                                                   'classified'])

    def test_has_archiving(self):
        """Test whether an NotImplementedError exception is thrown"""

        b = Backend('test')

        with self.assertRaises(NotImplementedError):
            b.has_archiving()

    def test_has_resuming(self):
        """Test whether an NotImplementedError exception is thrown"""

        b = Backend('test')

        with self.assertRaises(NotImplementedError):
            b.has_resuming()

    def test_metadata_id(self):
        """Test whether an NotImplementedError exception is thrown"""

        b = Backend('test')

        with self.assertRaises(NotImplementedError):
            b.metadata_id(None)

    def test_metadata_updated_on(self):
        """Test whether an NotImplementedError exception is thrown"""

        b = Backend('test')

        with self.assertRaises(NotImplementedError):
            b.metadata_updated_on(None)

    def test_metadata_category(self):
        """Test whether an NotImplementedError exception is thrown"""

        b = Backend('test')

        with self.assertRaises(NotImplementedError):
            b.metadata_category(None)

    def test_default_search_fields(self):
        """Test whether the default search field is `item_id`"""

        b = MockedBackend('test')

        expected = [
            {
                "item_id": "0"
            },
            {
                "item_id": "1"
            },
            {
                "item_id": "2"
            },
            {
                "item_id": "3"
            },
            {
                "item_id": "4"
            },
        ]

        for pos, item in enumerate(b.fetch()):
            self.assertDictEqual(item['search_fields'], expected[pos])

    def test_extra_search_fields(self):
        """Test whether the extra search fields are properly set"""

        b = MockedBackend('test')
        b.EXTRA_SEARCH_FIELDS = {
            'pos': ['item']
        }

        expected = [
            {
                "item_id": "0",
                "pos": 0
            },
            {
                "item_id": "1",
                "pos": 1
            },
            {
                "item_id": "2",
                "pos": 2
            },
            {
                "item_id": "3",
                "pos": 3
            },
            {
                "item_id": "4",
                "pos": 4
            },
        ]

        for pos, item in enumerate(b.fetch()):
            self.assertDictEqual(item['search_fields'], expected[pos])

    def test_extra_search_fields_skipped(self):
        """Test whether the search field is not listed if the field is not found/empty"""

        b = MockedBackend('test')
        b.EXTRA_SEARCH_FIELDS = {
            'pos': ['unknown']
        }

        expected = [
            {
                "item_id": "0"
            },
            {
                "item_id": "1"
            },
            {
                "item_id": "2"
            },
            {
                "item_id": "3"
            },
            {
                "item_id": "4"
            }
        ]

        for pos, item in enumerate(b.fetch()):
            self.assertDictEqual(item['search_fields'], expected[pos])

        b.EXTRA_SEARCH_FIELDS = {
            'pos': []
        }

        expected = [
            {
                "item_id": "0"
            },
            {
                "item_id": "1"
            },
            {
                "item_id": "2"
            },
            {
                "item_id": "3"
            },
            {
                "item_id": "4"
            }
        ]

        for pos, item in enumerate(b.fetch()):
            self.assertDictEqual(item['search_fields'], expected[pos])

    def test_tag(self):
        """Test whether tag value is initializated"""

        b = Backend('test')
        self.assertEqual(b.origin, 'test')
        self.assertEqual(b.tag, 'test')

        b = Backend('test', tag='mytag')
        self.assertEqual(b.origin, 'test')
        self.assertEqual(b.tag, 'mytag')

    def test_archive(self):
        """Test whether archive value is initializated"""

        archive_path = os.path.join(self.test_path, 'myarchive')
        archive = Archive.create(archive_path)

        b = Backend('test', archive=archive)
        self.assertEqual(b.archive, archive)

        b = Backend('test')
        self.assertEqual(b.archive, None)

        b.archive = archive
        self.assertEqual(b.archive, archive)

    def test_archive_value_error(self):
        """Test whether it raises a error on invalid archive istances"""

        with self.assertRaises(ValueError):
            Backend('test', archive=8)

        b = Backend('test')

        with self.assertRaises(ValueError):
            b.archive = 8

    def test_summary(self):
        """Test whether the summary is properly calculated"""

        b = MockedBackend('test')

        _ = [item for item in b.fetch()]

        self.assertEqual(b.summary.fetched, 5)
        self.assertIsNone(b.summary.extras)
        self.assertIsNone(b.summary.min_offset)
        self.assertIsNone(b.summary.max_offset)
        self.assertIsNone(b.summary.last_offset)
        self.assertEqual(b.summary.min_updated_on.isoformat(), '2016-01-01T00:00:00+00:00')
        self.assertEqual(b.summary.max_updated_on.isoformat(), '2016-01-01T00:00:04+00:00')
        self.assertEqual(b.summary.last_updated_on.isoformat(), '2016-01-01T00:00:04+00:00')
        self.assertEqual(b.summary.last_uuid, '82475202a5efc42c75add425c47ac032340f4f3d')
        self.assertEqual(b.summary.skipped, 0)
        self.assertEqual(b.summary.total, 5)

    def test_init_archive(self):
        """Test whether the archive is properly initialized when executing the fetch method"""

        archive_path = os.path.join(self.test_path, 'myarchive')
        archive = Archive.create(archive_path)
        b = MockedBackend('test', archive=archive)

        _ = [item for item in b.fetch()]

        self.assertEqual(b.archive.backend_name, b.__class__.__name__)
        self.assertEqual(b.archive.backend_version, b.version)
        self.assertEqual(b.archive.origin, b.origin)
        self.assertEqual(b.archive.category, MockedBackend.DEFAULT_CATEGORY)

    def test_fetch_wrong_category(self):
        """Check that an error is thrown if the category is not valid"""

        b = MockedBackend('test')

        with self.assertRaises(BackendError):
            _ = [item for item in b.fetch(category="acme")]

    def test_fetch_client_not_provided(self):
        """Test whether an NotImplementedError exception is thrown"""

        b = Backend('test')
        b.CATEGORIES = [MockedBackend.DEFAULT_CATEGORY]

        with self.assertRaises(NotImplementedError):
            _ = [item for item in b.fetch(category=MockedBackend.DEFAULT_CATEGORY)]

    def test_init_client_not_implemented(self):
        """Test whether an NotImplementedError exception is thrown"""

        b = Backend('test')

        with self.assertRaises(NotImplementedError):
            b._init_client()

    def test_fetch_items(self):
        """Test whether an NotImplementedError exception is thrown"""

        b = Backend('test')

        with self.assertRaises(NotImplementedError):
            b.fetch_items(MockedBackend.DEFAULT_CATEGORY)


class TestClassifiedFieldsFiltering(unittest.TestCase):
    """Unit tests for Backend filtering classified fields"""

    def setUp(self):
        self.test_path = tempfile.mkdtemp(prefix='perceval_')

    def tearDown(self):
        shutil.rmtree(self.test_path)

    def test_fetch_filtering_classified_fields(self):
        """Test whether classified fields are removed from the items"""

        backend = ClassifiedFieldsBackend('http://example.com/', tag='test')

        items = [item for item in backend.fetch(category=ClassifiedFieldsBackend.DEFAULT_CATEGORY,
                                                filter_classified=True)]

        for x in range(5):
            item = items[x]

            expected_uuid = uuid('http://example.com/', str(x))
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['category'], ClassifiedFieldsBackend.DEFAULT_CATEGORY)
            self.assertEqual(item['classified_fields_filtered'],
                             ['my.list_classified.dict_classified.field',
                              'my.classified.field', 'classified'])

            # Fields in CLASSIFIED_FIELDS are deleted
            expected = {
                'category': 'mock_item',
                'item': x,
                'my': {
                    'classified': {},
                    'field': x,
                    'list_classified': [{'dict_classified': {}, 'field': x}]
                }
            }
            self.assertDictEqual(item['data'], expected)

    def test_fetch_filtering_not_active(self):
        """Test whether classified fields are not removed from the items"""

        backend = ClassifiedFieldsBackend('http://example.com/', tag='test')

        items = [item for item in backend.fetch(category=ClassifiedFieldsBackend.DEFAULT_CATEGORY,
                                                filter_classified=False)]

        for x in range(5):
            item = items[x]

            expected_uuid = uuid('http://example.com/', str(x))
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['category'], ClassifiedFieldsBackend.DEFAULT_CATEGORY)
            self.assertEqual(item['classified_fields_filtered'], None)

            # Fields in CLASSIFIED_FIELDS are not deleted
            expected = {
                'category': 'mock_item',
                'classified': x,
                'item': x,
                'my': {
                    'classified': {'field': x},
                    'field': x,
                    'list_classified': [{'dict_classified': {'field': x}, 'field': x}]
                },
            }
            self.assertDictEqual(item['data'], expected)

    def test_fetch_filtering_empty_list(self):
        """Test whether no data is removed when classified fields list is empty"""

        backend = ClassifiedFieldsBackend('http://example.com/', tag='test')
        backend.CLASSIFIED_FIELDS = []

        items = [item for item in backend.fetch(category=ClassifiedFieldsBackend.DEFAULT_CATEGORY,
                                                filter_classified=True)]

        for x in range(5):
            item = items[x]

            expected_uuid = uuid('http://example.com/', str(x))
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['category'], ClassifiedFieldsBackend.DEFAULT_CATEGORY)
            self.assertEqual(item['classified_fields_filtered'], [])

            expected = {
                'category': 'mock_item',
                'classified': x,
                'item': x,
                'my': {
                    'classified': {'field': x},
                    'field': x,
                    'list_classified': [{'dict_classified': {'field': x}, 'field': x}]
                },
            }
            self.assertDictEqual(item['data'], expected)

    def test_error_archive_and_filter_classified(self):
        """Check if an error is raised when archive and classified fields filtering are both active"""

        archive_path = os.path.join(self.test_path, 'myarchive')
        archive = Archive.create(archive_path)

        backend = ClassifiedFieldsBackend('http://example.com/', archive=archive)

        msg_error = "classified fields filtering is not compatible with archiving items"

        with self.assertRaisesRegex(BackendError, msg_error):
            _ = [item for item in backend.fetch(category=ClassifiedFieldsBackend.DEFAULT_CATEGORY,
                                                filter_classified=True)]

    def test_not_found_field(self):
        """Check if items are fetched when a classified field does not exist"""

        backend = NotFoundClassifiedFieldBackend('http://example.com/', tag='test')

        with self.assertLogs(backend_logger, level='DEBUG') as cm:
            items = [item for item in backend.fetch(category=ClassifiedFieldsBackend.DEFAULT_CATEGORY,
                                                    filter_classified=True)]

            for x in range(5):
                item = items[x]

                # Classified fields are deleted; those not found are ignored
                expected = {
                    'category': 'mock_item',
                    'item': x
                }
                self.assertDictEqual(item['data'], expected)

                # Check logger output
                # Each classified-field-related message appears after 7 debug messages
                # because there are other debug messages
                _num_debug_msgs = 7
                expected_uuid = uuid('http://example.com/', str(x))
                exp = "Classified field 'classified_field' not found for item " + expected_uuid
                self.assertRegex(cm.output[x * _num_debug_msgs + 1], exp)


class TestBackendBlacklist(unittest.TestCase):

    def setUp(self):
        self.test_path = tempfile.mkdtemp(prefix='perceval_')

    def tearDown(self):
        shutil.rmtree(self.test_path)

    def test_fetch_blacklist(self):
        """Check whether blacklist items are filtered out"""

        backend = MockedBackendBlacklist('http://example.com/')
        items = [item for item in backend.fetch(category=MockedBackendBlacklist.DEFAULT_CATEGORY)]
        self.assertEqual(len(items), 5)

        backend = MockedBackendBlacklist('http://example.com/', blacklist_ids=[1])

        with self.assertLogs(backend_logger, level='INFO') as cm:
            items = [item for item in backend.fetch(category=MockedBackendBlacklist.DEFAULT_CATEGORY)]
            self.assertEqual(cm.output[0], 'WARNING:perceval.backend:Skipping blacklisted item item 1')

        self.assertEqual(len(items), 4)
        self.assertNotIn(1, [i['data']['item'] for i in items])

    def test_fetch_blacklist_no_unique_field(self):
        """Check whether no items are blacklisted if the ORIGIN_UNIQUE_FIELD is not defined"""

        backend = MockedBackendBlacklist('http://example.com/')
        items = [item for item in backend.fetch(category=MockedBackendBlacklist.DEFAULT_CATEGORY)]
        self.assertEqual(len(items), 5)

        backend = MockedBackendBlacklistNoOriginUniqueField('http://example.com/', blacklist_ids=[1])
        items = [item for item in backend.fetch(category=MockedBackendBlacklistNoOriginUniqueField.DEFAULT_CATEGORY)]
        self.assertEqual(len(items), 5)


class TestBackendArchive(TestCaseBackendArchive):
    """Unit tests for Backend using the archive"""

    def setUp(self):
        super().setUp()
        self.backend_write_archive = MockedBackend('test', archive=self.archive)
        self.backend_read_archive = MockedBackend('test', archive=self.archive)

    def tearDown(self):
        shutil.rmtree(self.test_path)

    def test_fetch_from_archive(self):
        """Test whether the method fetch_from_archive works properly"""

        self._test_fetch_from_archive()

    def test_fetch_from_archive_not_provided(self):
        """Test whether an exception is thrown when an archive is not provided"""

        b = MockedBackend('test')

        with self.assertRaises(ArchiveError):
            _ = [item for item in b.fetch_from_archive()]

    def test_fetch_client_not_implemented(self):
        """Test whether an NotImplementedError exception is thrown"""

        b = Backend('test', archive=self.archive)

        with self.assertRaises(NotImplementedError):
            _ = [item for item in b.fetch_from_archive()]


class TestBackendCommandArgumentParser(unittest.TestCase):
    """Unit tests for BackendCommandArgumentParser"""

    def test_argument_parser(self):
        """Test if an argument parser object is created on initialization"""

        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND)
        self.assertIsInstance(parser.parser, argparse.ArgumentParser)

    def test_backend(self):
        """Test whether _backend is initialized"""

        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND)
        self.assertEqual(parser._backend, MockedBackendCommand.BACKEND)

    def test_parse_default_args(self):
        """Test if the default configured arguments are parsed"""

        args = ['--tag', 'test', '--filter-classified']

        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND)

        parsed_args = parser.parse(*args)

        self.assertIsInstance(parsed_args, argparse.Namespace)
        self.assertEqual(parsed_args.tag, 'test')
        self.assertEqual(parsed_args.filter_classified, True)

    def test_parse_default_filter_classified(self):
        """Test default value of filter-classified options"""

        args = []

        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND)
        parsed_args = parser.parse(*args)

        self.assertEqual(parsed_args.filter_classified, False)

    def test_parse_with_aliases(self):
        """Test if a set of aliases is created after parsing"""

        aliases = {
            'label': 'tag',
            'label2': 'tag',
            'newdate': 'from_date',
            'from_date': 'tag',
            'notfound': 'backend_token'
        }
        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND,
                                              from_date=True,
                                              aliases=aliases)

        args = ['--tag', 'test', '--from-date', '2015-01-01']
        parsed_args = parser.parse(*args)

        expected_dt = datetime.datetime(2015, 1, 1, 0, 0,
                                        tzinfo=dateutil.tz.tzutc())

        self.assertIsInstance(parsed_args, argparse.Namespace)
        self.assertEqual(parsed_args.tag, 'test')
        self.assertEqual(parsed_args.from_date, expected_dt)

        # Check aliases
        self.assertEqual(parsed_args.label, 'test')
        self.assertEqual(parsed_args.label2, 'test')
        self.assertEqual(parsed_args.newdate, expected_dt)
        self.assertNotIn('notfound', parsed_args)

    def test_parse_date_args(self):
        """Test if date parameters are parsed"""

        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND,
                                              from_date=True,
                                              to_date=True)

        # Check default value
        args = []
        parsed_args = parser.parse(*args)

        self.assertEqual(parsed_args.from_date, DEFAULT_DATETIME)
        self.assertEqual(parsed_args.to_date, None)

        # Check argument 'from-date'
        args = ['--from-date', '2015-01-01']
        parsed_args = parser.parse(*args)

        expected = datetime.datetime(2015, 1, 1, 0, 0,
                                     tzinfo=dateutil.tz.tzutc())
        self.assertEqual(parsed_args.from_date, expected)
        self.assertEqual(parsed_args.to_date, None)

        # Invalid 'from-date'
        args = ['--from-date', 'asdf']

        with self.assertRaises(InvalidDateError):
            parsed_args = parser.parse(*args)

        # Check argument 'to-date'
        args = ['--to-date', '2016-01-01']
        parsed_args = parser.parse(*args)

        expected_dt = datetime.datetime(2016, 1, 1, 0, 0,
                                        tzinfo=dateutil.tz.tzutc())
        self.assertEqual(parsed_args.from_date, DEFAULT_DATETIME)
        self.assertEqual(parsed_args.to_date, expected_dt)

        # Invalid 'to-date'
        args = ['--to-date', 'asdf']

        with self.assertRaises(InvalidDateError):
            parsed_args = parser.parse(*args)

        # Check both arguments
        args = ['--from-date', '2015-01-01', '--to-date', '2016-01-01']
        parsed_args = parser.parse(*args)

        self.assertEqual(parsed_args.from_date, expected)
        self.assertEqual(parsed_args.to_date, expected_dt)

    def test_parse_offset_arg(self):
        """Test if offset parameter is parsed"""

        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND,
                                              offset=True)

        # Check default value
        args = []
        parsed_args = parser.parse(*args)

        self.assertEqual(parsed_args.offset, 0)

        # Check argument
        args = ['--offset', '88']
        parsed_args = parser.parse(*args)

        self.assertEqual(parsed_args.offset, 88)

    def test_parse_ssl_verify_arg(self):
        """Test if the ssl_verify parameter is parsed"""

        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND,
                                              ssl_verify=True)

        # Check default value
        args = []
        parsed_args = parser.parse(*args)

        self.assertTrue(parsed_args.ssl_verify)

        # Check argument
        args = ['--no-ssl-verify']
        parsed_args = parser.parse(*args)

        self.assertEqual(parsed_args.ssl_verify, False)

    def test_incompatible_date_and_offset(self):
        """Test if date and offset arguments are incompatible"""

        with self.assertRaises(AttributeError):
            _ = BackendCommandArgumentParser(MockedBackendCommand.BACKEND,
                                             from_date=True,
                                             offset=True)
        with self.assertRaises(AttributeError):
            _ = BackendCommandArgumentParser(MockedBackendCommand.BACKEND,
                                             to_date=True,
                                             offset=True)
        with self.assertRaises(AttributeError):
            _ = BackendCommandArgumentParser(MockedBackendCommand.BACKEND,
                                             from_date=True,
                                             to_date=True,
                                             offset=True)

    def test_parse_auth_args(self):
        """Test if the authtentication arguments are parsed"""

        args = ['-u', 'jsmith', '-p', '1234', '-t', 'abcd']

        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND,
                                              basic_auth=True,
                                              token_auth=True)
        parsed_args = parser.parse(*args)

        self.assertIsInstance(parsed_args, argparse.Namespace)
        self.assertEqual(parsed_args.user, 'jsmith')
        self.assertEqual(parsed_args.password, '1234')
        self.assertEqual(parsed_args.api_token, 'abcd')

    def test_parse_archive_args(self):
        """Test if achiving arguments are parsed"""

        args = ['--archive-path', '/tmp/archive',
                '--fetch-archive',
                '--archived-since', '2016-01-01',
                '--category', 'mocked']

        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND,
                                              archive=True)
        parsed_args = parser.parse(*args)

        expected_dt = datetime.datetime(2016, 1, 1, 0, 0,
                                        tzinfo=dateutil.tz.tzutc())

        self.assertIsInstance(parsed_args, argparse.Namespace)
        self.assertEqual(parsed_args.archive_path, '/tmp/archive')
        self.assertEqual(parsed_args.fetch_archive, True)
        self.assertEqual(parsed_args.no_archive, False)
        self.assertEqual(parsed_args.archived_since, expected_dt)

    def test_incompatible_fetch_archive_and_no_archive(self):
        """Test if fetch-archive and no-archive arguments are incompatible"""

        args = ['--fetch-archive', '--no-archive']
        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND,
                                              archive=True)

        with self.assertRaises(AttributeError):
            _ = parser.parse(*args)

    def test_default_category(self):
        """Test whether a default category is set if none is provided"""

        # No category is provided
        args = []
        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND,
                                              archive=True)
        parsed_args = parser.parse(*args)

        self.assertEqual(parsed_args.category, MockedBackendCommand.BACKEND.DEFAULT_CATEGORY)

    def test_specific_category(self):
        """Test whether a specific category is set when provided"""

        # An empty string is parsed
        args = ['--category', '']
        parser = BackendCommandArgumentParser(MockedBackendCommand.BACKEND,
                                              archive=True)
        parsed_args = parser.parse(*args)

        self.assertEqual(parsed_args.category, '')


def convert_cmd_output_to_json(filepath):
    """Transforms the output of a BackendCommand into json objects"""

    with open(filepath) as fout:
        buff = None

        for line in fout.readlines():
            if line.startswith('{\n'):
                buff = line
            elif line.startswith('}\n'):
                buff += line
                obj = json.loads(buff)
                yield obj
            else:
                buff += line


class TestBackendCommand(unittest.TestCase):
    """Unit tests for BackendCommand"""

    def setUp(self):
        self.test_path = tempfile.mkdtemp(prefix='perceval_')
        self.fout_path = tempfile.mktemp(dir=self.test_path)

    def tearDown(self):
        shutil.rmtree(self.test_path)

    def test_init(self):
        """Test if the arguments are parsed when the class is initialized with
        the default `_pre_init` and `_post_init` methods
        """
        args = ['-u', 'jsmith', '-p', '1234', '-t', 'abcd',
                '--category', 'mock_item', '--filter-classified',
                '--archive-path', self.test_path,
                '--fetch-archive', '--archived-since', '2015-01-01',
                '--from-date', '2015-01-01', '--tag', 'test',
                '--output', self.fout_path, 'http://example.com/']

        cmd = MockedBackendCommandDefaultPrePostInit(*args)
        self.assertIsInstance(cmd.parsed_args, argparse.Namespace)
        self.assertEqual(cmd.parsed_args.user, 'jsmith')
        self.assertEqual(cmd.parsed_args.password, '1234')
        self.assertEqual(cmd.parsed_args.api_token, 'abcd')
        self.assertEqual(cmd.parsed_args.archive_path, self.test_path)
        self.assertEqual(cmd.parsed_args.fetch_archive, True)
        self.assertEqual(cmd.parsed_args.tag, 'test')
        self.assertEqual(cmd.parsed_args.filter_classified, True)

    def test_parsing_on_init(self):
        """Test if the arguments are parsed when the class is initialized"""

        args = ['-u', 'jsmith', '-p', '1234', '-t', 'abcd',
                '--category', 'mock_item', '--filter-classified',
                '--archive-path', self.test_path,
                '--fetch-archive', '--archived-since', '2015-01-01',
                '--from-date', '2015-01-01', '--tag', 'test',
                '--output', self.fout_path, 'http://example.com/']

        dt_expected = datetime.datetime(2015, 1, 1, 0, 0,
                                        tzinfo=dateutil.tz.tzutc())

        cmd = MockedBackendCommand(*args)

        self.assertIsInstance(cmd.parsed_args, argparse.Namespace)
        self.assertEqual(cmd.parsed_args.user, 'jsmith')
        self.assertEqual(cmd.parsed_args.password, '1234')
        self.assertEqual(cmd.parsed_args.api_token, 'abcd')
        self.assertEqual(cmd.parsed_args.archive_path, self.test_path)
        self.assertEqual(cmd.parsed_args.fetch_archive, True)
        self.assertEqual(cmd.parsed_args.archived_since, dt_expected)
        self.assertEqual(cmd.parsed_args.from_date, dt_expected)
        self.assertEqual(cmd.parsed_args.tag, 'test')
        self.assertEqual(cmd.parsed_args.filter_classified, True)

        self.assertIsInstance(cmd.outfile, io.TextIOWrapper)
        self.assertEqual(cmd.outfile.name, self.fout_path)

        manager = cmd.archive_manager
        self.assertIsInstance(manager, ArchiveManager)
        self.assertEqual(manager.dirpath, self.test_path)

        cmd.outfile.close()

    def test_setup_cmd_parser(self):
        """Test whether an NotImplementedError exception is thrown"""

        with self.assertRaises(NotImplementedError):
            BackendCommand.setup_cmd_parser()

    @unittest.mock.patch('os.path.expanduser')
    def test_archive_manager_on_init(self, mock_expanduser):
        """Test if the archive manager is set when the class is initialized"""

        mock_expanduser.return_value = self.test_path

        args = ['-u', 'jsmith', '-p', '1234', '-t', 'abcd',
                '--from-date', '2015-01-01', '--tag', 'test',
                '--output', self.fout_path, 'http://example.com/']

        cmd = MockedBackendCommand(*args)

        manager = cmd.archive_manager
        self.assertIsInstance(manager, ArchiveManager)
        self.assertEqual(os.path.exists(manager.dirpath), True)
        self.assertEqual(manager.dirpath, self.test_path)

        # Due to '--no-archive' is given, Archive Manager isn't set
        args = ['-u', 'jsmith', '-p', '1234', '-t', 'abcd',
                '--no-archive', '--from-date', '2015-01-01',
                '--tag', 'test', '--output', self.fout_path,
                'http://example.com/']

        cmd = MockedBackendCommand(*args)
        self.assertEqual(cmd.archive_manager, None)

    def test_pre_init(self):
        """Test if pre_init method is called during initialization"""

        args = ['http://example.com/']

        cmd = MockedBackendCommand(*args)
        self.assertEqual(cmd.parsed_args.pre_init, True)

    def test_post_init(self):
        """Test if post_init method is called during initialization"""

        args = ['http://example.com/']

        cmd = MockedBackendCommand(*args)
        self.assertEqual(cmd.parsed_args.post_init, True)

    def test_run_default_category(self):
        """Test run method"""

        args = ['-u', 'jsmith', '-p', '1234', '-t', 'abcd',
                '--archive-path', self.test_path, '--category', MockedBackend.DEFAULT_CATEGORY,
                '--subtype', 'mocksubtype',
                '--from-date', '2015-01-01', '--tag', 'test',
                '--output', self.fout_path, 'http://example.com/']

        cmd = MockedBackendCommand(*args)
        cmd.run()
        cmd.outfile.close()

        items = [item for item in convert_cmd_output_to_json(self.fout_path)]

        self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['category'], MockedBackend.DEFAULT_CATEGORY)
            self.assertEqual(item['classified_fields_filtered'], None)

    def test_run_other_category(self):
        """Test whether when the category (different from the default one) is properly set"""

        args = ['-u', 'jsmith', '-p', '1234', '-t', 'abcd',
                '--archive-path', self.test_path, '--category', MockedBackend.OTHER_CATEGORY,
                '--subtype', 'mocksubtype',
                '--from-date', '2015-01-01', '--tag', 'test',
                '--output', self.fout_path, 'http://example.com/']

        cmd = MockedBackendCommand(*args)
        cmd.run()
        cmd.outfile.close()

        items = [item for item in convert_cmd_output_to_json(self.fout_path)]

        self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['category'], MockedBackend.OTHER_CATEGORY)
            self.assertEqual(item['classified_fields_filtered'], None)

    def test_run_no_category(self):
        """Test whether when the category is not passed, the default one is used"""

        args = ['-u', 'jsmith', '-p', '1234', '-t', 'abcd',
                '--archive-path', self.test_path,
                '--subtype', 'mocksubtype',
                '--from-date', '2015-01-01', '--tag', 'test',
                '--output', self.fout_path, 'http://example.com/']

        cmd = MockedBackendCommand(*args)
        cmd.run()
        cmd.outfile.close()

        items = [item for item in convert_cmd_output_to_json(self.fout_path)]

        self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['category'], MockedBackend.DEFAULT_CATEGORY)
            self.assertEqual(item['classified_fields_filtered'], None)

    def test_run_fetch_from_archive(self):
        """Test whether the command runs when fetch from archive is set"""

        args = ['--archive-path', self.test_path,
                '--from-date', '2015-01-01', '--tag', 'test',
                '--category', 'mock_item',
                '--subtype', 'mocksubtype',
                '--output', self.fout_path, 'http://example.com/']

        cmd = MockedBackendCommand(*args)
        cmd.run()
        cmd.outfile.close()

        items = [item for item in convert_cmd_output_to_json(self.fout_path)]

        self.assertEqual(len(items), 5)

        args = ['--archive-path', self.test_path, '--fetch-archive',
                '--from-date', '2015-01-01', '--tag', 'test', '--category', 'mock_item',
                '--subtype', 'mocksubtype',
                '--output', self.fout_path, 'http://example.com/']

        cmd = MockedBackendCommand(*args)
        cmd.run()
        cmd.outfile.close()

        items = [item for item in convert_cmd_output_to_json(self.fout_path)]
        self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            # ArchiveMockedBackend sets 'archive' value when
            # 'fetch-archive' option is set. This helps to know
            # the code is really running
            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['data']['archive'], True)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['classified_fields_filtered'], None)

    def test_run_no_archive(self):
        """Test whether the command runs when archive is not set"""

        args = ['--no-archive', '--from-date', '2015-01-01',
                '--tag', 'test', '--output', self.fout_path,
                '--category', 'mock_item',
                'http://example.com/']

        cmd = MockedBackendCommand(*args)
        cmd.run()
        cmd.outfile.close()

        items = [item for item in convert_cmd_output_to_json(self.fout_path)]

        self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['classified_fields_filtered'], None)

    def test_run_not_supported_archive(self):
        """Test whether the comand runs when archive is not supported"""

        args = ['--from-date', '2015-01-01',
                '--tag', 'test', '--output', self.fout_path,
                '--category', 'mock_item',
                'http://example.com/']

        cmd = NoArchiveBackendCommand(*args)
        cmd.run()
        cmd.outfile.close()

        items = [item for item in convert_cmd_output_to_json(self.fout_path)]

        self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['classified_fields_filtered'], None)

    def test_run_json_line(self):
        """Test run method with --json-line"""

        args = ['-u', 'jsmith', '-p', '1234', '-t', 'abcd',
                '--archive-path', self.test_path,
                '--from-date', '2015-01-01', '--tag', 'test',
                '--output', self.fout_path, 'http://example.com/',
                '--json-line']

        cmd = MockedBackendCommand(*args)
        cmd.run()
        cmd.outfile.close()

        with open(self.fout_path) as fout:
            items = fout.readlines()

        self.assertEqual(len(items), 5)

        for x in range(5):
            item = json.loads(items[x])
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['category'], MockedBackend.DEFAULT_CATEGORY)
            self.assertEqual(item['classified_fields_filtered'], None)

    def test_filter_classified_fields(self):
        """Test if fields are filtered with filter-classified option is active"""

        args = ['-u', 'jsmith', '-p', '1234', '-t', 'abcd',
                '--category', ClassifiedFieldsBackend.DEFAULT_CATEGORY,
                '--subtype', 'mocksubtype',
                '--from-date', '2015-01-01', '--tag', 'test',
                '--filter-classified', '--no-archive',
                '--output', self.fout_path, 'http://example.com/']

        cmd = ClassifiedFieldsBackendCommand(*args)
        cmd.run()
        cmd.outfile.close()

        items = [item for item in convert_cmd_output_to_json(self.fout_path)]

        self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['category'], ClassifiedFieldsBackend.DEFAULT_CATEGORY)
            self.assertEqual(item['classified_fields_filtered'],
                             ['my.list_classified.dict_classified.field',
                              'my.classified.field', 'classified'])

            expected = {
                'category': 'mock_item',
                'item': x,
                'my': {
                    'classified': {},
                    'field': x,
                    'list_classified': [{'dict_classified': {}, 'field': x}],
                },
            }
            self.assertDictEqual(item['data'], expected)

    def test_summary_logging(self):
        """Test if the summary is written to the log"""

        args = ['-u', 'jsmith', '-p', '1234', '-t', 'abcd',
                '--archive-path', self.test_path, '--category', MockedBackend.DEFAULT_CATEGORY,
                '--subtype', 'mocksubtype',
                '--from-date', '2015-01-01', '--tag', 'test',
                '--output', self.fout_path, 'http://example.com/']

        with self.assertLogs('perceval.backend', level='INFO') as cm:
            cmd = MockedBackendCommand(*args)
            cmd.run()
            cmd.outfile.close()

            items = [item for item in convert_cmd_output_to_json(self.fout_path)]

            self.assertEqual(len(items), 5)

            # The last message should be the summary output
            self.assertEqual(cm.output[-1], SUMMARY_LOG_REPORT)

    def test_blacklist_ids(self):
        """Test whether items are blacklisted when their IDs are passed via the command line"""

        args = ['http://example.com/',
                '--category', MockedBackendBlacklist.DEFAULT_CATEGORY,
                '--blacklist-ids', '2', '3', '4',
                '--output', self.fout_path]

        cmd = MockedBackendBlacklistCommand(*args)
        cmd.run()
        cmd.outfile.close()

        items = [item for item in convert_cmd_output_to_json(self.fout_path)]

        self.assertEqual(len(items), 2)

        for x in range(2):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['category'], MockedBackendBlacklist.DEFAULT_CATEGORY)

    def test_blacklist_ids_exception(self):
        """Test whether an exception is thrown when OriginUniqueField is not defined"""

        args = ['http://example.com/',
                '--category', MockedBackendBlacklistNoOriginUniqueField.DEFAULT_CATEGORY,
                '--blacklist-ids', '2', '3', '4',
                '--output', self.fout_path]

        with self.assertRaises(BackendCommandArgumentParserError):
            _ = MockedBackendBlacklistCommandNoOriginUniqueField(*args)


class TestBackendItemsGenerator(unittest.TestCase):
    """Unit tests for BackendItemsGenerator"""

    def setUp(self):
        self.test_path = tempfile.mkdtemp(prefix='perceval_')

    def tearDown(self):
        shutil.rmtree(self.test_path)

    def test_init_items(self):
        """Test whether a set of items is returned"""

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        with BackendItemsGenerator(CommandBackend, args, category, manager=None) as big:
            self.assertIsInstance(big, BackendItemsGenerator)
            items = [item for item in big.items]

            self.assertEqual(big.backend.origin, args['origin'])
            self.assertEqual(big.backend.tag, args['tag'])
            self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['classified_fields_filtered'], None)

    def test_init_items_from_archive(self):
        """Test whether a set of items is fetched from the archive"""

        manager = ArchiveManager(self.test_path)

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        with BackendItemsGenerator(CommandBackend, args, category, manager=manager) as big:
            self.assertIsInstance(big, BackendItemsGenerator)
            items = [item for item in big.items]
            self.assertEqual(big.backend.origin, args['origin'])
            self.assertEqual(big.backend.tag, args['tag'])
            self.assertEqual(len(items), 5)

        with BackendItemsGenerator(CommandBackend, args, category, manager=manager) as big:
            self.assertIsInstance(big, BackendItemsGenerator)
            items = [item for item in big.items]
            self.assertEqual(big.backend.origin, args['origin'])
            self.assertEqual(big.backend.tag, args['tag'])
            self.assertEqual(len(items), 5)

        with BackendItemsGenerator(CommandBackend, args, category,
                                   manager=manager, fetch_archive=True,
                                   archived_after=str_to_datetime('1970-01-01')) as big:
            self.assertIsInstance(big, BackendItemsGenerator)
            items = [item for item in big.items]

        self.assertEqual(len(items), 10)

        for x in range(2):
            for y in range(5):
                item = items[y + (x * 5)]
                expected_uuid = uuid('http://example.com/', str(y))

                self.assertEqual(item['data']['item'], y)
                self.assertEqual(item['data']['archive'], True)
                self.assertEqual(item['origin'], 'http://example.com/')
                self.assertEqual(item['uuid'], expected_uuid)
                self.assertEqual(item['tag'], 'test')
                self.assertEqual(item['classified_fields_filtered'], None)

    def test_init_items_from_archive_after(self):
        """Test if only those items archived after a date are returned"""

        manager = ArchiveManager(self.test_path)

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        with BackendItemsGenerator(CommandBackend, args, category, manager=manager) as big:
            self.assertIsInstance(big, BackendItemsGenerator)
            items = [item for item in big.items]
            self.assertEqual(big.backend.origin, args['origin'])
            self.assertEqual(big.backend.tag, args['tag'])
            self.assertEqual(len(items), 5)

        archived_dt = datetime_utcnow()

        with BackendItemsGenerator(CommandBackend, args, category, manager=manager) as big:
            self.assertIsInstance(big, BackendItemsGenerator)
            items = [item for item in big.items]
            self.assertEqual(big.backend.origin, args['origin'])
            self.assertEqual(big.backend.tag, args['tag'])
            self.assertEqual(len(items), 5)

        # Fetch items from the archive
        with BackendItemsGenerator(CommandBackend, args, category,
                                   manager=manager, fetch_archive=True,
                                   archived_after=str_to_datetime('1970-01-01')) as big:
            self.assertIsInstance(big, BackendItemsGenerator)
            items = [item for item in big.items]
            self.assertEqual(big.backend.origin, args['origin'])
            self.assertEqual(big.backend.tag, args['tag'])
            self.assertEqual(len(items), 10)

        # Fetch items archived after the given date
        with BackendItemsGenerator(CommandBackend, args, category,
                                   manager=manager, fetch_archive=True,
                                   archived_after=archived_dt) as big:
            self.assertIsInstance(big, BackendItemsGenerator)
            items = [item for item in big.items]
            self.assertEqual(len(items), 5)

    def test_init_items_filter_classified_fields(self):
        """Test whether classified fields are removed from the items"""

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        with BackendItemsGenerator(ClassifiedFieldsBackend, args, category,
                                   filter_classified=True, manager=None) as big:
            items = [item for item in big.items]

        self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]

            expected_uuid = uuid('http://example.com/', str(x))
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['classified_fields_filtered'],
                             ['my.list_classified.dict_classified.field',
                              'my.classified.field', 'classified'])

            # Fields in CLASSIFIED_FIELDS are deleted
            expected = {
                'category': 'mock_item',
                'item': x,
                'my': {
                    'classified': {},
                    'field': x,
                    'list_classified': [{'dict_classified': {}, 'field': x}],
                }
            }
            self.assertDictEqual(item['data'], expected)

    def test_init_archive_on_error(self):
        """Test whether an archive is removed when an unhandled exception occurs"""

        manager = ArchiveManager(self.test_path)

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        with self.assertRaises(BackendError):
            big = BackendItemsGenerator(ErrorCommandBackend, args, category, manager=manager)
            _ = [item for item in big.items]

        filepaths = manager.search('http://example.com/', 'ErrorCommandBackend',
                                   'mock_item', str_to_datetime('1970-01-01'))

        self.assertEqual(len(filepaths), 0)

    def test_init_no_archived_items(self):
        """Test when no archived items are available"""

        manager = ArchiveManager(self.test_path)

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        with BackendItemsGenerator(CommandBackend, args, category, manager=manager) as big:
            items = [item for item in big.items]

        self.assertEqual(len(items), 5)

        # There aren't items for this category
        with BackendItemsGenerator(CommandBackend, args, 'alt_item',
                                   manager=manager, fetch_archive=True,
                                   archived_after=str_to_datetime('1970-01-01')) as big:
            items = [item for item in big.items]
            self.assertEqual(len(items), 0)

    def test_init_ignore_corrupted_archive(self):
        """Check if a corrupted archive is ignored while fetching from archive"""

        def delete_rows(db, table_name):
            conn = sqlite3.connect(db)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM " + table_name)
            cursor.close()
            conn.commit()

        manager = ArchiveManager(self.test_path)

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        # First, fetch the items twice to check if several archive
        # are used
        with BackendItemsGenerator(CommandBackend, args, category, manager=manager) as big:
            items = [item for item in big.items]
            self.assertEqual(len(items), 5)

        with BackendItemsGenerator(CommandBackend, args, category, manager=manager) as big:
            items = [item for item in big.items]
            self.assertEqual(len(items), 5)

        # Find archive names to delete the rows of one of them to make it
        # corrupted
        filepaths = manager.search('http://example.com/', 'CommandBackend',
                                   category, str_to_datetime('1970-01-01'))
        self.assertEqual(len(filepaths), 2)

        to_remove = filepaths[0]
        delete_rows(to_remove, 'archive')

        # Fetch items from the archive
        with BackendItemsGenerator(CommandBackend, args, category,
                                   manager=manager, fetch_archive=True,
                                   archived_after=str_to_datetime('1970-01-01')) as big:
            items = [item for item in big.items]
            self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['data']['archive'], True)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['classified_fields_filtered'], None)

    def test_summary(self):
        """Test whether the method summary properly works"""

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        with BackendItemsGenerator(CommandBackend, args, category, manager=None) as big:
            self.assertIsInstance(big, BackendItemsGenerator)
            _ = [item for item in big.items]

            summary = big.summary
            self.assertEqual(summary.fetched, 5)
            self.assertEqual(summary.skipped, 0)
            self.assertEqual(summary.total, 5)
            self.assertEqual(summary.min_updated_on.timestamp(), 1451606400.0)
            self.assertEqual(summary.max_updated_on.timestamp(), 1451606404.0)
            self.assertEqual(summary.last_updated_on.timestamp(), 1451606404.0)
            self.assertEqual(summary.last_uuid, "6130c145435d661565bd7d402be403bea7cfb6b5")
            self.assertIsNone(summary.min_offset)
            self.assertIsNone(summary.max_offset)


class TestSummary(unittest.TestCase):
    """Unit tests for Summary"""

    def test_init(self):
        """Test whether the attributes are correctly initialized"""

        summary = Summary()

        self.assertEqual(summary.fetched, 0)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.total, 0)
        self.assertIsNone(summary.min_updated_on)
        self.assertIsNone(summary.max_updated_on)
        self.assertIsNone(summary.last_updated_on)
        self.assertIsNone(summary.last_uuid)
        self.assertIsNone(summary.min_offset)
        self.assertIsNone(summary.max_offset)
        self.assertIsNone(summary.last_offset)
        self.assertIsNone(summary.extras)

    def test_update(self):
        """Test whether the method update properly works"""

        items = [
            {
                "updated_on": 1483228800.0,
                "uuid": "0fa16dc4edab9130a14914a8d797f634d13b4ff4"
            },
            {
                "updated_on": 1483228900.0,
                "uuid": "0fa16dc4edab9130a14914a8d797f634d13b4aa4"
            },
            {
                "updated_on": 1483228700.0,
                "uuid": "0fa16dc4edab9130a14914a8d797f634d13b4bb4"
            }
        ]

        summary = Summary()

        item = items[0]
        summary.update(item)
        self.assertEqual(summary.fetched, 1)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.min_updated_on.timestamp(), 1483228800.0)
        self.assertEqual(summary.max_updated_on.timestamp(), 1483228800.0)
        self.assertEqual(summary.last_updated_on.timestamp(), 1483228800.0)
        self.assertEqual(summary.last_uuid, "0fa16dc4edab9130a14914a8d797f634d13b4ff4")
        self.assertIsNone(summary.min_offset)
        self.assertIsNone(summary.max_offset)
        self.assertIsNone(summary.last_offset)

        item = items[1]
        summary.update(item)
        self.assertEqual(summary.fetched, 2)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.min_updated_on.timestamp(), 1483228800.0)
        self.assertEqual(summary.max_updated_on.timestamp(), 1483228900.0)
        self.assertEqual(summary.last_updated_on.timestamp(), 1483228900.0)
        self.assertEqual(summary.last_uuid, "0fa16dc4edab9130a14914a8d797f634d13b4aa4")
        self.assertIsNone(summary.min_offset)
        self.assertIsNone(summary.max_offset)
        self.assertIsNone(summary.last_offset)

        item = items[2]
        summary.update(item)
        self.assertEqual(summary.fetched, 3)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.min_updated_on.timestamp(), 1483228700.0)
        self.assertEqual(summary.max_updated_on.timestamp(), 1483228900.0)
        self.assertEqual(summary.last_updated_on.timestamp(), 1483228700.0)
        self.assertEqual(summary.last_uuid, "0fa16dc4edab9130a14914a8d797f634d13b4bb4")
        self.assertIsNone(summary.min_offset)
        self.assertIsNone(summary.max_offset)
        self.assertIsNone(summary.last_offset)

    def test_update_offset(self):
        """Test whether the method update properly works on offset attributes"""

        items = [
            {
                "updated_on": 1483228800.0,
                "uuid": "0fa16dc4edab9130a14914a8d797f634d13b4ff4",
                "offset": 0
            },
            {
                "updated_on": 1483228900.0,
                "uuid": "0fa16dc4edab9130a14914a8d797f634d13b4aa4",
                "offset": 2
            },
            {
                "updated_on": 1483228700.0,
                "uuid": "0fa16dc4edab9130a14914a8d797f634d13b4bb4",
                "offset": 1
            },
        ]

        summary = Summary()

        item = items[0]
        summary.update(item)
        self.assertEqual(summary.fetched, 1)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.min_updated_on.timestamp(), 1483228800.0)
        self.assertEqual(summary.max_updated_on.timestamp(), 1483228800.0)
        self.assertEqual(summary.last_updated_on.timestamp(), 1483228800.0)
        self.assertEqual(summary.last_uuid, "0fa16dc4edab9130a14914a8d797f634d13b4ff4")
        self.assertEqual(summary.min_offset, 0)
        self.assertEqual(summary.max_offset, 0)
        self.assertEqual(summary.last_offset, 0)

        item = items[1]
        summary.update(item)
        self.assertEqual(summary.fetched, 2)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.min_updated_on.timestamp(), 1483228800.0)
        self.assertEqual(summary.max_updated_on.timestamp(), 1483228900.0)
        self.assertEqual(summary.last_updated_on.timestamp(), 1483228900.0)
        self.assertEqual(summary.last_uuid, "0fa16dc4edab9130a14914a8d797f634d13b4aa4")
        self.assertEqual(summary.min_offset, 0)
        self.assertEqual(summary.max_offset, 2)
        self.assertEqual(summary.last_offset, 2)

        item = items[2]
        summary.update(item)
        self.assertEqual(summary.fetched, 3)
        self.assertEqual(summary.skipped, 0)
        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.min_updated_on.timestamp(), 1483228700.0)
        self.assertEqual(summary.max_updated_on.timestamp(), 1483228900.0)
        self.assertEqual(summary.last_updated_on.timestamp(), 1483228700.0)
        self.assertEqual(summary.last_uuid, "0fa16dc4edab9130a14914a8d797f634d13b4bb4")
        self.assertEqual(summary.min_offset, 0)
        self.assertEqual(summary.max_offset, 2)
        self.assertEqual(summary.last_offset, 1)


class TestMetadata(unittest.TestCase):
    """Test metadata method"""

    def test_metadata(self):
        backend = MockedBackend('test', 'mytag')
        before = datetime_utcnow().timestamp()
        items = [item for item in backend.fetch()]
        after = datetime_utcnow().timestamp()

        for x in range(5):
            item = items[x]

            expected_uuid = uuid('test', str(x))
            expected_updated_on = 1451606400.0 + item['data']['item']

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['backend_name'], 'MockedBackend')
            self.assertEqual(item['backend_version'], '0.2.0')
            self.assertEqual(item['perceval_version'], __version__)
            self.assertEqual(item['origin'], 'test')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['updated_on'], expected_updated_on)
            self.assertEqual(item['category'], 'mock_item')
            self.assertEqual(item['classified_fields_filtered'], None)
            self.assertEqual(item['tag'], 'mytag')
            self.assertGreater(item['timestamp'], before)
            self.assertLess(item['timestamp'], after)

            before = item['timestamp']

    def test_metadata_classified_fields(self):
        backend = MockedBackend('test', 'mytag')
        before = datetime_utcnow().timestamp()
        items = [item for item in backend.fetch(filter_classified=True)]
        after = datetime_utcnow().timestamp()

        for x in range(5):
            item = items[x]

            expected_uuid = uuid('test', str(x))
            expected_updated_on = 1451606400.0 + item['data']['item']

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['backend_name'], 'MockedBackend')
            self.assertEqual(item['backend_version'], '0.2.0')
            self.assertEqual(item['perceval_version'], __version__)
            self.assertEqual(item['origin'], 'test')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['updated_on'], expected_updated_on)
            self.assertEqual(item['category'], 'mock_item')
            self.assertEqual(item['classified_fields_filtered'], [])
            self.assertEqual(item['tag'], 'mytag')
            self.assertGreater(item['timestamp'], before)
            self.assertLess(item['timestamp'], after)

            before = item['timestamp']


class TestUUID(unittest.TestCase):
    """Unit tests for uuid function"""

    def test_uuid(self):
        """Check whether the function returns the expected UUID"""

        result = uuid('1', '2', '3', '4')
        self.assertEqual(result, 'e7b71c81f5a0723e2237f157dba81777ce7c6c21')

        result = uuid('http://example.com/', '1234567')
        self.assertEqual(result, '47509b2f0d4ffc513ca9230838a69aa841d7f055')

    def test_non_str_value(self):
        """Check whether a UUID cannot be generated when a given value is not a str"""

        self.assertRaises(ValueError, uuid, '1', '2', 3, '4')
        self.assertRaises(ValueError, uuid, 0, '1', '2', '3')
        self.assertRaises(ValueError, uuid, '1', '2', '3', 4.0)

    def test_none_value(self):
        """Check whether a UUID cannot be generated when a given value is None"""

        self.assertRaises(ValueError, uuid, '1', '2', None, '3')
        self.assertRaises(ValueError, uuid, None, '1', '2', '3')
        self.assertRaises(ValueError, uuid, '1', '2', '3', None)

    def test_empty_value(self):
        """Check whether a UUID cannot be generated when a given value is empty"""

        self.assertRaises(ValueError, uuid, '1', '', '2', '3')
        self.assertRaises(ValueError, uuid, '', '1', '2', '3')
        self.assertRaises(ValueError, uuid, '1', '2', '3', '')


class TestFetch(unittest.TestCase):
    """Unit tests for fetch function"""

    def setUp(self):
        self.test_path = tempfile.mkdtemp(prefix='perceval_')

    def tearDown(self):
        shutil.rmtree(self.test_path)

    def test_items(self):
        """Test whether a set of items is returned"""

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        items = fetch(CommandBackend, args, category, manager=None)
        items = [item for item in items]

        self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['classified_fields_filtered'], None)

    def test_items_storing_archive(self):
        """Test whether items are stored in an archive"""

        manager = ArchiveManager(self.test_path)

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        items = fetch(CommandBackend, args, category, manager=manager)
        items = [item for item in items]

        self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['classified_fields_filtered'], None)

        filepaths = manager.search('http://example.com/', 'CommandBackend',
                                   'mock_item', str_to_datetime('1970-01-01'))

        self.assertEqual(len(filepaths), 1)

        archive = Archive(filepaths[0])
        self.assertEqual(archive._count_table_rows('archive'), 5)

    def test_filter_classified_fields(self):
        """Test whether classified fields are removed from the items"""

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        items = fetch(ClassifiedFieldsBackend, args, category,
                      filter_classified=True, manager=None)
        items = [item for item in items]

        self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]

            expected_uuid = uuid('http://example.com/', str(x))
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['classified_fields_filtered'],
                             ['my.list_classified.dict_classified.field',
                              'my.classified.field', 'classified'])

            # Fields in CLASSIFIED_FIELDS are deleted
            expected = {
                'category': 'mock_item',
                'item': x,
                'my': {
                    'classified': {},
                    'field': x,
                    'list_classified': [{'dict_classified': {}, 'field': x}],
                }
            }
            self.assertDictEqual(item['data'], expected)

    def test_remove_archive_on_error(self):
        """Test whether an archive is removed when an unhandled exception occurs"""

        manager = ArchiveManager(self.test_path)

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        items = fetch(ErrorCommandBackend, args, category, manager=manager)

        with self.assertRaises(BackendError):
            _ = [item for item in items]

        filepaths = manager.search('http://example.com/', 'ErrorCommandBackend',
                                   'mock_item', str_to_datetime('1970-01-01'))

        self.assertEqual(len(filepaths), 0)


class TestFetchFromArchive(unittest.TestCase):
    """Unit tests for fetch_from_archive function"""

    def setUp(self):
        self.test_path = tempfile.mkdtemp(prefix='perceval_')

    def tearDown(self):
        shutil.rmtree(self.test_path)

    def test_archive(self):
        """Test whether a set of items is fetched from the archive"""

        manager = ArchiveManager(self.test_path)

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        # First, fetch the items twice to check if several archive
        # are used
        items = fetch(CommandBackend, args, category, manager=manager)
        items = [item for item in items]
        self.assertEqual(len(items), 5)

        items = fetch(CommandBackend, args, category, manager=manager)
        items = [item for item in items]
        self.assertEqual(len(items), 5)

        # Fetch items from the archive
        items = fetch_from_archive(CommandBackend, args, manager,
                                   category, str_to_datetime('1970-01-01'))
        items = [item for item in items]

        self.assertEqual(len(items), 10)

        for x in range(2):
            for y in range(5):
                item = items[y + (x * 5)]
                expected_uuid = uuid('http://example.com/', str(y))

                self.assertEqual(item['data']['item'], y)
                self.assertEqual(item['data']['archive'], True)
                self.assertEqual(item['origin'], 'http://example.com/')
                self.assertEqual(item['uuid'], expected_uuid)
                self.assertEqual(item['tag'], 'test')
                self.assertEqual(item['classified_fields_filtered'], None)

    def test_archived_after(self):
        """Test if only those items archived after a date are returned"""

        manager = ArchiveManager(self.test_path)

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        items = fetch(CommandBackend, args, category, manager=manager)
        items = [item for item in items]
        self.assertEqual(len(items), 5)

        archived_dt = datetime_utcnow()

        items = fetch(CommandBackend, args, category, manager=manager)
        items = [item for item in items]
        self.assertEqual(len(items), 5)

        # Fetch items from the archive
        items = fetch_from_archive(CommandBackend, args, manager,
                                   category, str_to_datetime('1970-01-01'))
        items = [item for item in items]
        self.assertEqual(len(items), 10)

        # Fetch items archived after the given date
        items = fetch_from_archive(CommandBackend, args, manager,
                                   category, archived_dt)
        items = [item for item in items]
        self.assertEqual(len(items), 5)

    def test_no_archived_items(self):
        """Test when no archived items are available"""

        manager = ArchiveManager(self.test_path)

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        items = fetch(CommandBackend, args, category, manager=manager)
        items = [item for item in items]
        self.assertEqual(len(items), 5)

        # There aren't items for this category
        items = fetch_from_archive(CommandBackend, args, manager,
                                   'alt_item', str_to_datetime('1970-01-01'))
        items = [item for item in items]
        self.assertEqual(len(items), 0)

    def test_ignore_corrupted_archive(self):
        """Check if a corrupted archive is ignored while fetching from archive"""

        def delete_rows(db, table_name):
            conn = sqlite3.connect(db)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM " + table_name)
            cursor.close()
            conn.commit()

        manager = ArchiveManager(self.test_path)

        category = 'mock_item'
        args = {
            'origin': 'http://example.com/',
            'tag': 'test',
            'subtype': 'mocksubtype',
            'from-date': str_to_datetime('2015-01-01')
        }

        # First, fetch the items twice to check if several archive
        # are used
        items = fetch(CommandBackend, args, category, manager=manager)
        items = [item for item in items]
        self.assertEqual(len(items), 5)

        items = fetch(CommandBackend, args, category, manager=manager)
        items = [item for item in items]
        self.assertEqual(len(items), 5)

        # Find archive names to delete the rows of one of them to make it
        # corrupted
        filepaths = manager.search('http://example.com/', 'CommandBackend',
                                   category, str_to_datetime('1970-01-01'))
        self.assertEqual(len(filepaths), 2)

        to_remove = filepaths[0]
        delete_rows(to_remove, 'archive')

        # Fetch items from the archive
        items = fetch_from_archive(CommandBackend, args, manager,
                                   category, str_to_datetime('1970-01-01'))
        items = [item for item in items]

        self.assertEqual(len(items), 5)

        for x in range(5):
            item = items[x]
            expected_uuid = uuid('http://example.com/', str(x))

            self.assertEqual(item['data']['item'], x)
            self.assertEqual(item['data']['archive'], True)
            self.assertEqual(item['origin'], 'http://example.com/')
            self.assertEqual(item['uuid'], expected_uuid)
            self.assertEqual(item['tag'], 'test')
            self.assertEqual(item['classified_fields_filtered'], None)


class TestFindBackends(unittest.TestCase):
    """Unit tests for find_backends function"""

    def test_find_backends(self):
        """Check that the backends and their commands are correctly found"""

        backends, backend_commands = find_backends(mocked_package)

        expected_backends = {
            'backend': BackendA,
            'nested_backend_b': BackendB,
            'nested_backend_c': BackendC
        }
        self.assertDictEqual(backends, expected_backends)

        expected_backend_commands = {
            'backend': BackendCommandA,
            'nested_backend_b': BackendCommandB,
            'nested_backend_c': BackendCommandC
        }
        self.assertDictEqual(backend_commands, expected_backend_commands)

    def test_find_backends_in_module(self):
        """Check that the backends and their commands are correctly found in a submodule"""

        backends, backend_commands = find_backends(mocked_package.nested_package)

        expected_backends = {
            'nested_backend_b': BackendB,
            'nested_backend_c': BackendC
        }
        self.assertDictEqual(backends, expected_backends)

        expected_backend_commands = {
            'nested_backend_b': BackendCommandB,
            'nested_backend_c': BackendCommandC
        }
        self.assertDictEqual(backend_commands, expected_backend_commands)


if __name__ == "__main__":
    unittest.main()
