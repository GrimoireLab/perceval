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
#     Santiago Dueñas <sduenas@bitergia.com>
#     Valerio Cosentino <valcos@bitergia.com>
#     Jesus M. Gonzalez-Barahona <jgb@gsyc.es>
#     Harshal Mittal <harshalmittal4@gmail.com>
#     JJMerchante <jj.merchante@gmail.com>
#     animesh <animuz111@gmail.com>
#

import argparse
import collections
import hashlib
import importlib
import json
import logging
import os
import pkgutil
import sys

from grimoirelab_toolkit.introspect import find_signature_parameters
from grimoirelab_toolkit.datetime import (datetime_utcnow,
                                          str_to_datetime,
                                          unixtime_to_datetime)
from .archive import Archive, ArchiveManager
from .errors import ArchiveError, BackendError, BackendCommandArgumentParserError
from ._version import __version__


logger = logging.getLogger(__name__)


ARCHIVES_DEFAULT_PATH = '~/.perceval/archives/'
DEFAULT_SEARCH_FIELD = 'item_id'

OriginUniqueField = collections.namedtuple('OriginUniqueField', 'name type')


class Backend:
    """Abstract class for backends.

    Base class to fetch data from a repository. This repository
    will be named as 'origin'. During the initialization, an `Archive`
    object can be provided for archiving raw data from the repositories.

    To avoid a :class:`NotImplementedError`, derived classes have to implement
    or define:
     - :func:`fetch_items`, to retrieve items from the repository
     - :func:`has_archiving`, whether this backend supports archives
     - :func:`has_resuming`, whether this backend supports resuming
     - :func:`metadata_id`, to produce a unique id from an item
     - :func:`metadata_updated_on`, to find the last time an item was modified
     - :func:`metadata_category`, to identify the category of an item
     - :func:`_init_client`, to initialize the backend's client
     - :data:`CATEGORIES`, defining the set of categories the backend produces
     - [Optional] :data:`CLASSIFIED_FIELDS`, to hide certain fields from results
     - [Optional] :data:`EXTRA_SEARCH_FIELDS`, to add easy access fields to items
     - [Optional] :data:`ORIGIN_UNIQUE_FIELD`, to enable item blacklisting

    For more information on the details of implementing these methods, please
    see the docs on each method.

    The fetched items can be tagged using the `tag` parameter. It will
    be useful to trace data. When it is set to `None` or to an empty
    string, the tag will be the same that the `origin` attribute.

    To track which version of the backend was used during the fetching
    process, this class provides a `version` attribute that each backend
    may override.

    Each fetch operation generates a summary, available via the property
    `summary`. By default, it includes the last UUID generated, number
    of items fetched, skipped and their sum, plus the min, max and last
    updated_on times. Furthermore, for backends using offsets, the
    corresponding summary contains the min and max offsets retrieved. Finally,
    the summary also includes some extra fields, which can be used by any
    backend to include fetch-specific information.

    Backends also produce a set of search fields, exposed in the
    `search_fields` attribute of each item returned by a call to :func:`fetch`.
    These contain the `item_id`, as well as any number of backend-specific
    fields.

    :param origin: identifier of the repository
    :param tag: tag items using this label
    :param archive: archive to store/retrieve data
    :param ssl_verify: enable/disable SSL verification

    :raises ValueError: raised when `archive` is not an instance of
        `Archive` class
    """
    version = '0.12.0'

    CATEGORIES = []
    """A list of categories that can be fetched by this backend.

    Every backend able to produce items falling into a limited set of
    categories. The specific categories a backend can fetch is unique to that
    backend.

    The categories defined in this variable (and *only* the categories defined
    in this variable) can be passed to :func:`fetch` and returned from
    :func:`metadata_category`.

    Implementing backends can define any category they need, as long as
    categories are short, descriptive, snake_case strings, such as "commit",
    "merge_request", or "pull_request".
    """

    CLASSIFIED_FIELDS = []
    """A list of fields that should be considered sensitive or confidential.

    Fields listed here will be hidden from fetched items, when this behaviour
    is requested.

    Fields are represented as a list of strings.  As items returned are dicts
    that may contain nested dicts, each entry is a list which stores the "path"
    or nested dicts keys to the field to remove. For example, `['my',
    'classified', 'field']` will remove `field` from
    `item['data']['my']['classified']` dict.

    Classified data filtering and archiving are not compatible to prevent
    data leaks or security issues.
    """

    EXTRA_SEARCH_FIELDS = {}
    """A set of search fields to simplify query operations.

    The use of search fields can avoid the manual inspection of the items. The
    search fields are included with items returned from :func:`fetch` in a dict
    with the following shape:

        {
            'key-1': value-1,
            'key-2': value-2,
            'key-3': value-3
        }

    These fields are added to the item metadata information in the
    `search_fields` attribute. By default, `search_fields` contains
    the id of the item ('item_id': item_id_value), obtained via the
    method `metadata_id`. However, each backend can set extra search
    fields using the dict :data:`EXTRA_SEARCH_FIELDS`. An example of
    :data:`EXTRA_SEARCH_FIELDS` is provided below:

        {
            'project_id': ['fields', 'project', 'id'],
            'project_key': ['fields', 'project', 'key'],
            'project_name': ['fields', 'project', 'name']
        }

    Each key in the dict is a search field to be included in the item
    metadata information, while the corresponding value is a list that
    stores the "path" of the search field value within the item.
    """

    ORIGIN_UNIQUE_FIELD = None
    """A field unique to a given origin for items produced by this backend.

    If `ORIGIN_UNIQUE_FIELD` is defined, users can pass a list of blocked
    values which should not be included in the results, if the field defined here
    contains them. For example, if `ORIGIN_UNIQUE_FIELD` were set to
    `post_id`, then users could pass a list of post ids that should be excluded
    from the results.

    If set to `None`, blacklisting will be disabled completely. Otherwise, this
    should be set to a :class:`OriginUniqueField` containing the number and
    data type of the field.

    Note: Origin in this context refers to one site, api, or other remote that
    contains several repositories, each consisting of many items of several
    categories. For example, for the backend GitLab, an origin would be one
    instance GitLab, such as gitlab.com or opensource.ieee.org, which each
    contain many repositories, which contain items such as issues and merge
    request.

    To access this field, please prefer :func:`origin_unique_field`.
    """

    def __init__(self, origin, tag=None, archive=None, blacklist_ids=None, ssl_verify=True):
        self._origin = origin
        self.tag = tag if tag else origin
        self.archive = archive or None
        self.blacklist_ids = blacklist_ids or None
        self._summary = None
        self._ssl_verify = ssl_verify

    @property
    def origin(self):
        return self._origin

    @property
    def summary(self):
        return self._summary

    @property
    def archive(self):
        return self._archive

    @property
    def ssl_verify(self):
        return self._ssl_verify

    @archive.setter
    def archive(self, obj):
        if obj and not isinstance(obj, Archive):
            msg = "obj is not an instance of Archive. %s object given" \
                % (str(type(obj)))
            raise ValueError(msg)

        self._archive = obj

    @property
    def categories(self):
        """See :data:`CATEGORIES`."""
        return self.CATEGORIES

    @property
    def origin_unique_field(self):
        """See :data:`ORIGIN_UNIQUE_FIELD`."""
        return self.ORIGIN_UNIQUE_FIELD

    @property
    def classified_fields(self):
        """A list of fields to be hidden from results.

        Fields are represented as a list of strings, where each string is a
        period delimited field. For example,
        `'attributes.author_info.secret_info'` would hide the secret info of the
        author in the attributes dict.
        """
        cfs = ['.'.join(cf) for cf in self.CLASSIFIED_FIELDS]
        return cfs

    def fetch_items(self, category, **kwargs):
        """Retrieve raw data from the repository.

        This method is to be implemented by implementors of Backend, and is
        intended for internal use. Developers hoping to retrieve processed
        results should use the :func:`fetch` method.

        This method receives a category of items to fetch from the repository.
        This will be one of categories defined in the :data:`CATEGORIES` class
        variable. The method also receives a list of keyword arguments. These
        arguments include any commandline variables defined by the
        corresponding :class:`BackendCommand`.

        The method is then responsible for retrieving all items matching the
        criteria defined by the keyword args and the given category, then
        returning them as a generator of dicts. The structure of the dicts is
        irrelevant, but each dict should represent exactly one item.

        :param category: the category if items to retrieve from the repository
        :param kwargs: additional arguments to assist or specify retrieval

        :returns: a generator producing items
        """
        raise NotImplementedError

    def fetch(self, category, filter_classified=False, **kwargs):
        """Fetch items from the repository.

        The method retrieves items from a repository.

        To removed classified fields from the resulting items, set
        the parameter `filter_classified`. Take into account this
        parameter is incompatible with archiving items. Raw client
        data are archived before any other process. Therefore,
        classified data  are stored within the archive. To prevent
        from possible data leaks or security issues when users do
        not need these fields, archiving and filtering are not
        compatible.

        :param category: the category of the items fetched
        :param filter_classified: remove classified fields from the resulting items
        :param kwargs: a list of other parameters (e.g., from_date, offset, etc.
        specific for each backend)

        :returns: a generator of items

        :raises BackendError: either when the category is not valid or
            'filter_classified' and 'archive' are active at the same time.
        """
        self._summary = Summary()

        if category not in self.categories:
            cause = "%s category not valid for %s" % (category, self.__class__.__name__)
            raise BackendError(cause=cause)

        if filter_classified and self.archive:
            cause = "classified fields filtering is not compatible with archiving items"
            raise BackendError(cause=cause)

        if self.archive:
            self.archive.init_metadata(self.origin, self.__class__.__name__, self.version, category,
                                       kwargs)

        self.client = self._init_client()

        for item in self.fetch_items(category, **kwargs):
            if filter_classified:
                item = self.filter_classified_data(item)

            metadata_item = self.metadata(item, filter_classified=filter_classified)
            self.summary.update(metadata_item)

            yield metadata_item

    def fetch_from_archive(self):
        """Fetch the questions from an archive.

        It returns the items stored within an archive. If this method is called but
        no archive was provided, the method will raise a `ArchiveError` exception.

        :returns: a generator of items

        :raises ArchiveError: raised when an error occurs accessing an archive
        """
        if not self.archive:
            raise ArchiveError(cause="archive instance was not provided")

        self._summary = Summary()
        self.client = self._init_client(from_archive=True)

        for item in self.fetch_items(self.archive.category, **self.archive.backend_params):
            metadata_item = self.metadata(item)
            self.summary.update(metadata_item)

            yield metadata_item

    def filter_classified_data(self, item):
        """Remove classified or confidential data from an item.

        It removes those fields that contain data considered as classified.
        Classified fields are defined in `CLASSIFIED_FIELDS` class attribute.

        :param item: fields will be removed from this item

        :returns: the same item but with confidential data filtered
        """
        item_uuid = uuid(self.origin, self.metadata_id(item))

        logger.debug("Filtering classified data for item %s", item_uuid)

        for cf in self.CLASSIFIED_FIELDS:
            try:
                _remove_key_from_nested_dictlist(item, cf)
            except KeyError:
                logger.debug("Classified field '%s' not found for item %s; field ignored",
                             '.'.join(cf), item_uuid)

        logger.debug("Classified data filtered for item %s", item_uuid)

        return item

    def search_fields(self, item):
        """Add search fields to an item.

        It adds the values of the fields defined in `SEARCH_FIELDS` class attribute with
        their corresponding keys.

        :param item: the item to extract the search fields values

        :returns: a dict of search fields
        """
        item_uuid = uuid(self.origin, self.metadata_id(item))

        logger.debug("Adding search fields to item %s", item_uuid)

        logger.debug("Adding default `item_id` search field to item %s", item_uuid)
        search_fields = {
            DEFAULT_SEARCH_FIELD: self.metadata_id(item)
        }

        logger.debug("Adding extra search fields to item %s", item_uuid)
        for sf in self.EXTRA_SEARCH_FIELDS:
            try:
                search_field = self.EXTRA_SEARCH_FIELDS[sf]
                field_value = _find_value_from_nested_dict(item, search_field)
                search_fields[sf] = field_value
            except KeyError:
                logger.warning("Extra search field '%s' not found for item %s; field ignored",
                               sf, item_uuid)
            except IndexError:
                logger.warning("Extra search field '%s' is empty %s; field ignored",
                               sf, item_uuid)

        logger.debug("Search fields added for item %s", item_uuid)

        return search_fields

    def metadata(self, item, filter_classified=False):
        """Add metadata to an item.

        It adds metadata to a given item such as how and
        when it was fetched. The contents from the original item will
        be stored under the 'data' keyword.

        :param item: an item fetched by a backend
        :param filter_classified: sets if classified fields were filtered
        """
        item = {
            'backend_name': self.__class__.__name__,
            'backend_version': self.version,
            'perceval_version': __version__,
            'timestamp': datetime_utcnow().timestamp(),
            'origin': self.origin,
            'uuid': uuid(self.origin, self.metadata_id(item)),
            'updated_on': self.metadata_updated_on(item),
            'classified_fields_filtered': self.classified_fields if filter_classified else None,
            'category': self.metadata_category(item),
            'search_fields': self.search_fields(item),
            'tag': self.tag,
            'data': item,
        }

        return item

    @classmethod
    def has_archiving(cls):
        """Whether or not this backend supports archiving requests.

        For implementors, this means whether :func:`_init_client` can be called
        with `from_archive=True` and whether the backend will respect that.  If
        the client used by the backend is an :class:`HttpClient`, and
        :func:`_init_client` passes `from_archive` on to the
        :class:`client.HttpClient`'s initializer, this should be true.

        Classified data filtering and archiving are not compatible to prevent
        data leaks or security issues.
        """
        raise NotImplementedError

    @classmethod
    def has_resuming(cls):
        """Whether this backend supports resuming interrupted collections.

        When interrupted, some backends may support resuming the collection by
        setting the `from_date` parameter on :func:`fetch_items` or
        :func:`fetch` to the date of the last item retrieved from the
        repository.

        However, for some backends, this cannot be done, for example because
        results are retrieved from newest to oldest. If resuming was attempted on a
        backend like this, then some items would be missed.

        For example, if the backend was in the middle of retrieving items from
        January 5th through 1st, but was interrupted when retrieving items from
        the 3rd, than it would be missing items for the 2nd and 1st. If this
        backend was resumed by setting `from_date` to the most recent item (the
        5th), these missing items would not be retrieved, since they are earlier
        than the `from_date`.

        This method is used to indicate that this backend can be resumed in this
        manner without missing any items. If a backend declares that it supports
        resuming, than  `from_date` should be set to the date of the *most
        recent item* from the last collection, even if it failed. Otherwise,
        `from_date` should be set to the most recent item of the last
        *successful* collection. Resuming in this manner should not leave any
        holes in the collected items.

        This can be used to speed up collections by skipping network IO for
        items that have already been downloaded and added to the database.

        Additionally, `from_date` may be set regardless of this setting if the
        last collection did not fail, or if the user is not interested in items
        earlier than the provided datetime.

        Implementers should return a constant `True` if their backend supports
        resuming connections in this manor, or `False` otherwise.
        """
        raise NotImplementedError

    @staticmethod
    def metadata_id(item):
        """Produce a unique identifier for an item.

        Given one of the items produced by :func:`fetch_items`, produce a
        unique identifier for that item. Typically, this is an identifier
        given by the repository itself, such as a commit hash or post id.

        The id should be represented by a string.
        """
        raise NotImplementedError

    @staticmethod
    def metadata_updated_on(item):
        """Determine the last time an item was updated.

        Given one of the items produced by :func:`fetch_items`, attempt to
        identify the last time this item was modified.

        :returns: The timestamp of the last modification, represented as epoch
            seconds (a UNIX timestamp)
        """
        raise NotImplementedError

    @staticmethod
    def metadata_category(item):
        """Identify the category of a given item.

        Every item returned by :func:`fetch_items` should belong to exactly one
        of the categories listed in the `CATEGORIES` class member. This method
        should determine which category the item belongs to, given one of the
        items returned by :func:`fetch_items` method.

        Note that for all items returned by a call to :func:`fetch_items`, this
        method is expected to return a category equivalent to the one passed as
        the `category` argument.

        :returns: One of the strings in `CATEGORIES`
        """
        raise NotImplementedError

    def _init_client(self, from_archive=False):
        """Initialize the client to be used by the backend.

        Many backends use a persistent HTTP client to retrieve information from
        a backend. This method is called before any calls to
        :func:`fetch_items`, and should be used as an opportunity to initialize
        the client.

        If the backend chooses to do so, then it should return an instance of
        its client, which will then be immediately assigned to the `.client`
        attribute of the backend.

        If the backend chooses not to initialize a client, it may simply `pass`
        or return `None`. However, note that the `.client` attribute will
        still be overridden.

        Additionally, the client or the backend should be set to respect
        archiving, based on the :func:`from_archive`. If the backend does not
        report that it :func:`has_archiving`, then this parameter may just be
        ignored.

        :returns: a client, or `None`
        """
        raise NotImplementedError

    def _skip_item(self, item):
        if not self.origin_unique_field:
            return False

        field_name = self.origin_unique_field.name
        if self.blacklist_ids and item[field_name] in self.blacklist_ids:
            logger.warning("Skipping blacklisted item %s %s", field_name, item[field_name])
            return True

        return False


def _find_value_from_nested_dict(nested_dict, path_to_field):
    if len(path_to_field) == 0:
        raise IndexError

    key = path_to_field[0]

    if len(path_to_field) == 1:
        value = nested_dict[key] if nested_dict else None
        return value
    else:
        return _find_value_from_nested_dict(nested_dict[key], path_to_field[1:])


def _remove_key_from_nested_dictlist(nested_dictlist, path_to_field):
    if len(path_to_field) == 0:
        return

    if isinstance(nested_dictlist, list):
        for item in nested_dictlist:
            _remove_key_from_nested_dictlist(item, path_to_field)
    else:
        key = path_to_field[0]
        if len(path_to_field) == 1:
            nested_dictlist.pop(key)
        else:
            _remove_key_from_nested_dictlist(nested_dictlist[key], path_to_field[1:])


class BackendCommandArgumentParser:
    """Manage and parse backend command arguments.

    This class defines and parses a set of arguments common to
    backends commands. Some parameters like archive or the different
    types of authentication can be set during the initialization
    of the instance.

    :param backend: backend object
    :param from_date: set from_date argument
    :param to_date: set to_date argument
    :param offset: set offset argument
    :param basic_auth: set basic authentication arguments
    :param token_auth: set token/key authentication arguments
    :param archive: set archiving arguments
    :param aliases: define aliases for parsed arguments
    :param ssl_verify: set SSL verify argument

    :raises AttributeError: when both `from_date` and `offset` are set
        to `True`
    """

    def __init__(self, backend, from_date=False, to_date=False, offset=False,
                 basic_auth=False, token_auth=False, archive=False,
                 aliases=None, blacklist=False, ssl_verify=False):
        self._from_date = from_date
        self._to_date = to_date
        self._archive = archive
        self._backend = backend
        self._ssl_verify = ssl_verify

        self.aliases = aliases or {}
        self.parser = argparse.ArgumentParser()

        group = self.parser.add_argument_group('general arguments')
        group.add_argument('--category', dest='category',
                           help="type of the items to fetch (%s)" % ','.join(self._backend.CATEGORIES))
        group.add_argument('--tag', dest='tag',
                           help="tag the items generated during the fetching process")
        group.add_argument('--filter-classified', dest='filter_classified',
                           action='store_true',
                           help="filter classified fields, if any, from fetched items")

        if (from_date or to_date) and offset:
            raise AttributeError("date and offset parameters are incompatible")

        if from_date:
            group.add_argument('--from-date', dest='from_date',
                               default='1970-01-01',
                               help="fetch items updated since this \
                                     date (in any ISO 8601 format, e.g., 'YYYY-MM-DD HH:mm:SS +|-HH:MM')")
        if to_date:
            group.add_argument('--to-date', dest='to_date',
                               help="fetch items updated before this \
                                    date (in any ISO 8601 format, e.g., 'YYYY-MM-DD HH:mm:SS +|-HH:MM')")
        if offset:
            group.add_argument('--offset', dest='offset',
                               type=int, default=0,
                               help="offset to start fetching items")
        if blacklist:
            if not backend.ORIGIN_UNIQUE_FIELD:
                msg = "Origin unique field not defined for {} backend".format(backend.__name__)
                raise BackendCommandArgumentParserError(cause=msg)

            group.add_argument('--blacklist-ids', dest='blacklist_ids',
                               nargs='*', type=backend.ORIGIN_UNIQUE_FIELD.type,
                               help="Ids (field: %s) of items that must not be retrieved." %
                                    backend.ORIGIN_UNIQUE_FIELD.name)

        if basic_auth or token_auth:
            self._set_auth_arguments(basic_auth=basic_auth,
                                     token_auth=token_auth)

        if archive:
            self._set_archive_arguments()

        if ssl_verify:
            group.add_argument('--no-ssl-verify', dest='ssl_verify', action='store_false',
                               help="disable SSL verification")

        self._set_output_arguments()

    def parse(self, *args):
        """Parse a list of arguments.

        Parse argument strings needed to run a backend command. The result
        will be a `argparse.Namespace` object populated with the values
        obtained after the validation of the parameters.

        :param args: argument strings

        :result: an object with the parsed values
        """
        parsed_args = self.parser.parse_args(args)

        # Ensure category is set
        if parsed_args.category is None:
            parsed_args.category = self._backend.CATEGORIES[0]

        if self._from_date:
            parsed_args.from_date = str_to_datetime(parsed_args.from_date)
        if self._to_date and parsed_args.to_date:
            parsed_args.to_date = str_to_datetime(parsed_args.to_date)
        if self._archive and parsed_args.archived_since:
            parsed_args.archived_since = str_to_datetime(parsed_args.archived_since)

        if self._archive and parsed_args.fetch_archive and parsed_args.no_archive:
            raise AttributeError("fetch-archive and no-archive arguments are not compatible")
        if self._archive and parsed_args.fetch_archive and not parsed_args.category:
            raise AttributeError("fetch-archive needs a category to work with")

        # Set aliases
        for alias, arg in self.aliases.items():
            if (alias not in parsed_args) and (arg in parsed_args):
                value = getattr(parsed_args, arg, None)
                setattr(parsed_args, alias, value)

        return parsed_args

    def _set_auth_arguments(self, basic_auth=True, token_auth=False):
        """Activate authentication arguments parsing"""

        group = self.parser.add_argument_group('authentication arguments')

        if basic_auth:
            group.add_argument('-u', '--backend-user', dest='user',
                               help="backend user")
            group.add_argument('-p', '--backend-password', dest='password',
                               help="backend password")
        if token_auth:
            group.add_argument('-t', '--api-token', dest='api_token',
                               help="backend authentication token / API key")

    def _set_archive_arguments(self):
        """Activate archive arguments parsing"""

        group = self.parser.add_argument_group('archive arguments')
        group.add_argument('--archive-path', dest='archive_path', default=None,
                           help="directory path to the archives")
        group.add_argument('--no-archive', dest='no_archive', action='store_true',
                           help="do not archive data")
        group.add_argument('--fetch-archive', dest='fetch_archive', action='store_true',
                           help="fetch data from the archives")
        group.add_argument('--archived-since', dest='archived_since', default='1970-01-01',
                           help="retrieve items archived since the given date")

    def _set_output_arguments(self):
        """Activate output arguments parsing"""

        group = self.parser.add_argument_group('output arguments')
        group.add_argument('-o', '--output', type=argparse.FileType('w'),
                           dest='outfile', default=sys.stdout,
                           help="output file")
        group.add_argument('--json-line', dest='json_line', action='store_true',
                           help="produce a JSON line for each output item")


class BackendCommand:
    """Abstract class to run backends from the command line.

    When the class is initialized, it parses the given arguments using
    the defined argument parser on `setup_cmd_parser` method. Those
    arguments will be stored in the attribute `parsed_args`.

    The arguments will be used to initialize and run the `Backend` object
    assigned to this command. The backend used to run the command is stored
    under `BACKEND` class attributed. Any class derived from this and must
    set its own `Backend` class.

    Moreover, the method `setup_cmd_parser` must be implemented to execute
    the backend.

    :param debug: boolean flag to check if application is running in debug mode
    """
    BACKEND = None

    def __init__(self, *args, debug=False):
        parser = self.setup_cmd_parser()

        parser.parser.prog = "perceval %s" % self.BACKEND.__name__.lower()

        self.parsed_args = parser.parse(*args)
        self.debug = debug
        self.archive_manager = None
        self._pre_init()
        self._initialize_archive()
        self._post_init()

        self.outfile = self.parsed_args.outfile
        self.json_line = self.parsed_args.json_line

    def run(self):
        """Fetch and write items.

        This method runs the backend to fetch the items from the given
        origin. Items are converted to JSON objects and written to the
        defined output. A summary with the result is written to the log.

        If `fetch-archive` parameter was given as an argument during
        the initialization of the instance, the items will be retrieved
        using the archive manager.
        """
        backend_args = vars(self.parsed_args)
        category = backend_args.pop('category', None)
        filter_classified = backend_args.pop('filter_classified', False)
        fetch_archive = self.archive_manager and self.parsed_args.fetch_archive
        archived_since = backend_args.pop('archived_since', None)

        with BackendItemsGenerator(self.BACKEND, backend_args, category,
                                   filter_classified=filter_classified,
                                   manager=self.archive_manager,
                                   fetch_archive=fetch_archive,
                                   archived_after=archived_since) as big:
            try:
                for item in big.items:
                    if self.json_line:
                        obj = json.dumps(item, separators=(',', ':'), sort_keys=True)
                    else:
                        obj = json.dumps(item, indent=4, sort_keys=True)
                    self.outfile.write(obj)
                    self.outfile.write('\n')

                self._log_summary(big.summary)
            except IOError as e:
                logger.exception(f"Error!: {e}", exc_info=self.debug)
            except Exception as e:
                logger.exception(f"Error!: {e}", exc_info=self.debug)

    def _pre_init(self):
        """Override to execute before backend is initialized."""
        pass

    def _post_init(self):
        """Override to execute after backend is initialized."""
        pass

    def _initialize_archive(self):
        """Initialize archive based on the parsed parameters."""

        if 'archive_path' not in self.parsed_args:
            manager = None
        elif self.parsed_args.no_archive:
            manager = None
        else:
            if not self.parsed_args.archive_path:
                archive_path = os.path.expanduser(ARCHIVES_DEFAULT_PATH)
            else:
                archive_path = self.parsed_args.archive_path

            manager = ArchiveManager(archive_path)

        self.archive_manager = manager

    def _log_summary(self, summary):
        """Write a formatted summary to the log."""

        template = (
            "Summary of results\n\n"
            "\t   Total items: \t{total}\n"
            "\tItems produced: \t{fetched}\n"
            "\t Items skipped: \t{skipped}\n"
            "\n"
            "\tLast item UUID: \t{last_uuid}\n"
            "\tLast item date: \t{last_updated_on}\n"
            "\n"
            "\tMin. item date: \t{min_updated_on}\n"
            "\tMax. item date: \t{max_updated_on}\n"
            "\n"
            "\tMin. offset: \t{min_offset}"
            "\tMax. offset: \t{max_offset}"
            "\tLast offset: \t{last_offset}\n"
            "\n"
        )

        values = {
            'total': summary.total,
            'fetched': summary.fetched,
            'skipped': summary.skipped,
            'last_uuid': summary.last_uuid or '-',
            'last_updated_on': summary.last_updated_on or '-',
            'min_updated_on': summary.min_updated_on or '-',
            'max_updated_on': summary.max_updated_on or '-',
            'min_offset': summary.min_offset or '-',
            'max_offset': summary.min_offset or '-',
            'last_offset': summary.last_offset or '-',

        }
        message = template.format(**values)

        logger.info(message)

    @classmethod
    def setup_cmd_parser(cls):
        raise NotImplementedError


class BackendItemsGenerator:
    """BackendItemsGenerator class.

    This class provides a generator through the `items` attribute that
    will fetch items from any data source and/or archive in a transparent
    way. A summary with the result of the process can be accessed via
    the attribute `summary`.

    To initialize an instance is necessary to pass the backend that will
    be used to fetch data, its parameters and other useful data as the
    category of the items to retrieve and the archive options.

    This object can also be used as a context manager.

    :param backend_class: backend class to fetch items
    :param backend_args: dict of arguments needed to fetch the items
    :param category: category of the items to retrieve
       If None, it will use the default backend category
    :param filter_classified: remove classified fields from the
        resulting items. Note that filter classified is not supported
        for archived items.
    :param manager: archive manager where the items will be retrieved
    :param fetch_archive: If enabled, items are fetched from archives
    :param archived_after: return items archived after this date
    """
    def __init__(self, backend_class, backend_args, category,
                 filter_classified=False, manager=None,
                 fetch_archive=False, archived_after=None):
        init_args = find_signature_parameters(backend_class.__init__,
                                              backend_args)

        if not fetch_archive:
            archive = manager.create_archive() if manager else None
            init_args['archive'] = archive
            self.backend = backend_class(**init_args)
            items = self.__fetch(backend_args, category,
                                 filter_classified=filter_classified,
                                 manager=manager)
        else:
            self.backend = backend_class(**init_args)
            items = self.__fetch_from_archive(category, manager, archived_after)

        self.items = items

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.backend = None
        self.items = None

    @property
    def summary(self):
        """Return the summary object of the last fetch execution"""

        return self.backend.summary

    def __fetch(self, backend_args, category, filter_classified=False,
                manager=None):
        """Fetch items using the given backend.

        Generator to get items using the backend. When an archive manager
        is given, this function will store the fetched items in an `Archive`.
        If an exception is raised, this archive will be removed to avoid
        corrupted archives.

        The parameters needed to get the items are given using the
        `backend_args` dict parameter.

        :param backend_args: dict of arguments needed to fetch the items
        :param category: category of the items to retrieve.
            If None, it will use the default backend category
        :param filter_classified: remove classified fields from the resulting
            items
        :param manager: archive manager needed to store the items

        :returns: a generator of items
        """
        if category:
            backend_args['category'] = category
        if filter_classified:
            backend_args['filter_classified'] = filter_classified

        fetch_args = find_signature_parameters(self.backend.fetch,
                                               backend_args)
        items = self.backend.fetch(**fetch_args)

        try:
            for item in items:
                yield item
        except Exception as e:
            if manager:
                archive_path = self.backend.archive.archive_path
                manager.remove_archive(archive_path)
            raise e

    def __fetch_from_archive(self, category, manager, archived_after):
        """Fetch items from an archive manager.

        Generator to get the items of a category (previously fetched
        by the backend) from an archive manager. Only those items
        archived after the given date will be returned.

        :param category: category of the items to retrieve
        :param manager: archive manager where the items will be retrieved
        :param archived_after: return items archived after this date

        :returns: a generator of archived items
        """
        filepaths = manager.search(self.backend.origin,
                                   self.backend.__class__.__name__,
                                   category,
                                   archived_after)

        for filepath in filepaths:
            self.backend.archive = Archive(filepath)
            items = self.backend.fetch_from_archive()

            try:
                for item in items:
                    yield item
            except ArchiveError as e:
                logger.warning("Ignoring %s archive due to: %s", filepath, str(e))


class Summary:
    """Summary class for fetch executions.

    This class models the summary of a fetch execution. It includes
    the last UUID, number of items fetched, skipped and their sum,
    plus the minimum, maximum and last updated_on times.

    Furthermore, for backends using offsets, the corresponding summary
    contains the minimum, maximum and last offsets retrieved.

    Finally, the summary also includes some extra fields, which can
    be used by any backend to include fetch-specific information.
    """
    def __init__(self):
        self.fetched = 0
        self.skipped = 0
        self.min_updated_on = None
        self.max_updated_on = None
        self.last_updated_on = None
        self.last_uuid = None
        self.min_offset = None
        self.max_offset = None
        self.last_offset = None
        self.extras = None

    @property
    def total(self):
        """Number of items retrieved. This includes fetched and skipped items."""

        return self.fetched + self.skipped

    def update(self, item):
        """Update the summary attributes by accessing the item data.

        :param item: a Perceval item
        """
        self.fetched += 1
        self.last_uuid = item['uuid']

        updated_on = unixtime_to_datetime(item['updated_on'])
        self.min_updated_on = updated_on if not self.min_updated_on else min(self.min_updated_on, updated_on)
        self.max_updated_on = updated_on if not self.max_updated_on else max(self.max_updated_on, updated_on)
        self.last_updated_on = updated_on

        offset = item.get('offset', None)
        if offset is not None:
            self.last_offset = offset
            self.min_offset = offset if self.min_offset is None else min(self.min_offset, offset)
            self.max_offset = offset if self.max_offset is None else max(self.max_offset, offset)


def uuid(*args):
    """Generate a UUID based on the given parameters.

    The UUID will be the SHA1 of the concatenation of the values
    from the list. The separator between these values is ':'.
    Each value must be a non-empty string, otherwise, the function
    will raise an exception.

    :param *args: list of arguments used to generate the UUID

    :returns: a universal unique identifier

    :raises ValueError: when anyone of the values is not a string,
        is empty or `None`.
    """
    def check_value(v):
        if not isinstance(v, str):
            raise ValueError("%s value is not a string instance" % str(v))
        elif not v:
            raise ValueError("value cannot be None or empty")
        else:
            return v

    s = ':'.join(map(check_value, args))

    sha1 = hashlib.sha1(s.encode('utf-8', errors='surrogateescape'))
    uuid_sha1 = sha1.hexdigest()

    return uuid_sha1


def fetch(backend_class, backend_args, category, filter_classified=False,
          manager=None):
    """Fetch items using the given backend.

    Generator to get items using the given backend class. When
    an archive manager is given, this function will store
    the fetched items in an `Archive`. If an exception is raised,
    this archive will be removed to avoid corrupted archives.

    The parameters needed to initialize the `backend` class and
    get the items are given using `backend_args` dict parameter.

    :param backend_class: backend class to fetch items
    :param backend_args: dict of arguments needed to fetch the items
    :param category: category of the items to retrieve.
       If None, it will use the default backend category
    :param filter_classified: remove classified fields from the resulting items
    :param manager: archive manager needed to store the items

    :returns: a generator of items
    """
    init_args = find_signature_parameters(backend_class.__init__,
                                          backend_args)
    archive = manager.create_archive() if manager else None
    init_args['archive'] = archive

    backend = backend_class(**init_args)

    if category:
        backend_args['category'] = category
    if filter_classified:
        backend_args['filter_classified'] = filter_classified

    fetch_args = find_signature_parameters(backend.fetch,
                                           backend_args)
    items = backend.fetch(**fetch_args)

    try:
        for item in items:
            yield item
    except Exception as e:
        if manager:
            archive_path = archive.archive_path
            manager.remove_archive(archive_path)
        raise e


def fetch_from_archive(backend_class, backend_args, manager,
                       category, archived_after):
    """Fetch items from an archive manager.

    Generator to get the items of a category (previously fetched
    by the given backend class) from an archive manager. Only those
    items archived after the given date will be returned.

    The parameters needed to initialize `backend` and get the
    items are given using `backend_args` dict parameter.

    :param backend_class: backend class to retrive items
    :param backend_args: dict of arguments needed to retrieve the items
    :param manager: archive manager where the items will be retrieved
    :param category: category of the items to retrieve
    :param archived_after: return items archived after this date

    :returns: a generator of archived items
    """
    init_args = find_signature_parameters(backend_class.__init__,
                                          backend_args)
    backend = backend_class(**init_args)

    filepaths = manager.search(backend.origin,
                               backend.__class__.__name__,
                               category,
                               archived_after)

    for filepath in filepaths:
        backend.archive = Archive(filepath)
        items = backend.fetch_from_archive()

        try:
            for item in items:
                yield item
        except ArchiveError as e:
            logger.warning("Ignoring %s archive due to: %s", filepath, str(e))


def find_backends(top_package):
    """Find available backends.

    Look for the Perceval backends and commands under `top_package`
    and its sub-packages. When `top_package` defines a namespace,
    backends under that same namespace will be found too.

    :param top_package: package storing backends

    :returns: a tuple with two dicts: one with `Backend` classes and one
        with `BackendCommand` classes
    """
    candidates = pkgutil.walk_packages(top_package.__path__,
                                       prefix=top_package.__name__ + '.')

    modules = [name for _, name, is_pkg in candidates if not is_pkg]

    return _import_backends(modules)


def _import_backends(modules):
    for module in modules:
        importlib.import_module(module)

    bkls = _find_classes(Backend, modules)
    ckls = _find_classes(BackendCommand, modules)

    backends = {name: kls for name, kls in bkls}
    commands = {name: klass for name, klass in ckls}

    return backends, commands


def _find_classes(parent, modules):
    parents = parent.__subclasses__()

    while parents:
        kls = parents.pop()

        m = kls.__module__

        if m not in modules:
            continue

        name = m.split('.')[-1]
        parents.extend(kls.__subclasses__())

        yield name, kls
