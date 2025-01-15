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
#     Assad Montasser <assad.montasser@ow2.org>
#     Valerio Cosentino <valcos@bitergia.com>
#     Santiago Dueñas <sduenas@bitergia.com>
#     Jesus M. Gonzalez-Barahona <jgb@gsyc.es>
#     Harshal Mittal <harshalmittal4@gmail.com>
#     JJMerchante <jj.merchante@gmail.com>
#

import json
import logging
import requests

import urllib.parse

from grimoirelab_toolkit.datetime import (datetime_to_utc,
                                          datetime_utcnow,
                                          str_to_datetime,
                                          unixtime_to_datetime)
from grimoirelab_toolkit.uris import urijoin

from ...backend import (Backend,
                        BackendCommand,
                        BackendCommandArgumentParser,
                        OriginUniqueField,
                        DEFAULT_SEARCH_FIELD)
from ...client import HttpClient, RateLimitHandler
from ...utils import DEFAULT_DATETIME
from ...errors import BackendError, HttpClientError

CATEGORY_ISSUE = "issue"
CATEGORY_MERGE_REQUEST = "merge_request"

GITLAB_URL = "https://gitlab.com/"
GITLAB_API_URL = "https://gitlab.com/api/v4"

# Range before sleeping until rate limit reset
MIN_RATE_LIMIT = 10
MAX_RATE_LIMIT = 500

# Default sleep time and retries to deal with connection/server problems
DEFAULT_SLEEP_TIME = 1
MAX_RETRIES = 5

DEFAULT_RETRY_AFTER_STATUS_CODES = [500, 502]

TARGET_ISSUE_FIELDS = ['user_notes_count', 'award_emoji']

logger = logging.getLogger(__name__)


class GitLab(Backend):
    """GitLab backend for Perceval.

    This class allows the fetch the issues stored in GitLab
    repository.

    :param owner: GitLab owner
    :param repository: GitLab repository from the owner
    :param api_token: GitLab auth token to access the API
    :param is_oauth_token: True if the token is OAuth (default False)
    :param base_url: GitLab URL; defaults to https://gitlab.com
    :param tag: label used to mark the data
    :param archive: archive to store/retrieve items
    :param sleep_for_rate: sleep until rate limit is reset
    :param min_rate_to_sleep: minimun rate needed to sleep until
         it will be reset
    :param max_retries: number of max retries to a data source
        before raising a RetryError exception
    :param sleep_time: time (in seconds) to sleep in case
        of connection problems
    :param blacklist_ids: ids of items that must not be retrieved
    :param extra_retry_after_status: retry HTTP requests after status (default 500 and 502). These status complete
        the ones (413, 429, 503) defined in the HttpClient class
    :param ssl_verify: enable/disable SSL verification
    """
    version = '1.0.0'

    CATEGORIES = [CATEGORY_ISSUE, CATEGORY_MERGE_REQUEST]
    ORIGIN_UNIQUE_FIELD = OriginUniqueField(name='iid', type=int)

    def __init__(self, owner=None, repository=None, api_token=None,
                 is_oauth_token=False, base_url=None, tag=None, archive=None,
                 sleep_for_rate=False, min_rate_to_sleep=MIN_RATE_LIMIT,
                 max_retries=MAX_RETRIES, sleep_time=DEFAULT_SLEEP_TIME,
                 blacklist_ids=None, extra_retry_after_status=None, ssl_verify=True):
        origin = base_url if base_url else GITLAB_URL
        origin = urijoin(origin, owner, repository)

        if not api_token and is_oauth_token:
            raise BackendError(cause="is_oauth_token is True but api_token is None")

        super().__init__(origin, tag=tag, archive=archive, ssl_verify=ssl_verify)
        self.base_url = base_url
        self.owner = owner
        self.repository = repository
        self.api_token = api_token
        self.is_oauth_token = is_oauth_token
        self.sleep_for_rate = sleep_for_rate
        self.min_rate_to_sleep = min_rate_to_sleep
        self.max_retries = max_retries
        self.sleep_time = sleep_time
        self.blacklist_ids = blacklist_ids
        self.client = None
        self.extra_retry_after_status = DEFAULT_RETRY_AFTER_STATUS_CODES if not extra_retry_after_status \
            else extra_retry_after_status
        self._users = {}  # internal users cache

    def search_fields(self, item):
        """Add search fields to an item.

        It adds the values of `metadata_id` plus the `owner`, `project`
        and `iid` of the issue or merge requests. Optionally, if the project
        is part of a (nested) group, all groups are also included to the search
        fields via the attribute `groups`.

        :param item: the item to extract the search fields values

        :returns: a dict of search fields
        """
        search_fields = {
            DEFAULT_SEARCH_FIELD: self.metadata_id(item),
            'owner': self.owner,
            'iid': item['iid'],
            'project': None,
            'groups': None
        }

        if '%2F' in self.repository:
            projects = self.repository.split('%2F')
            search_fields['project'] = projects[-1]
            search_fields['groups'] = projects[:-1]
        else:
            search_fields['project'] = self.repository

        return search_fields

    def fetch(self, category=CATEGORY_ISSUE, from_date=DEFAULT_DATETIME):
        """Fetch the issues or merge requests from the repository.

        The method retrieves, from a GitLab repository, the issues or merge requests
        updated since the given date.

        :param category: the category of items to fetch
        :param from_date: obtain items updated since this date

        :returns: a generator of items
        """
        if not from_date:
            from_date = DEFAULT_DATETIME

        from_date = datetime_to_utc(from_date)

        kwargs = {'from_date': from_date}
        items = super().fetch(category, **kwargs)

        return items

    def fetch_items(self, category, **kwargs):
        """Fetch the items (issues or merge_requests)

        :param category: the category of items to fetch
        :param kwargs: backend arguments

        :returns: a generator of items
        """
        from_date = kwargs['from_date']

        if category == CATEGORY_ISSUE:
            items = self.__fetch_issues(from_date)
        else:
            items = self.__fetch_merge_requests(from_date)

        return items

    @classmethod
    def has_archiving(cls):
        """Returns whether it supports archivng items on the fetch process.

        :returns: this backend supports items archive
        """
        return True

    @classmethod
    def has_resuming(cls):
        """Returns whether it supports to resume the fetch process.

        :returns: this backend does not support items resuming
        """
        return True

    @staticmethod
    def metadata_id(item):
        """Extracts the identifier from a GitLab item."""

        return str(item['id'])

    @staticmethod
    def metadata_updated_on(item):
        """Extracts the update time from a GitLab item.

        The timestamp used is extracted from 'updated_at' field.
        This date is converted to UNIX timestamp format. As GitLab
        dates are in UTC the conversion is straightforward.

        :param item: item generated by the backend

        :returns: a UNIX timestamp
        """
        ts = item['updated_at']
        ts = str_to_datetime(ts)

        return ts.timestamp()

    @staticmethod
    def metadata_category(item):
        """Extracts the category from a GitLab item.

        This backend only generates one type of item which is
        'issue'.
        """
        if "merged_by" in item:
            category = CATEGORY_MERGE_REQUEST
        else:
            category = CATEGORY_ISSUE

        return category

    def _init_client(self, from_archive=False):
        """Init client"""

        return GitLabClient(self.owner, self.repository, self.api_token,
                            self.is_oauth_token, self.base_url,
                            self.sleep_for_rate, self.min_rate_to_sleep,
                            self.sleep_time, self.max_retries, self.extra_retry_after_status,
                            self.archive, from_archive, self.ssl_verify)

    def __fetch_issues(self, from_date):
        """Fetch the issues"""

        issues_groups = self.client.issues(from_date=from_date)

        for raw_issues in issues_groups:
            issues = json.loads(raw_issues)
            for issue in issues:
                issue_id = issue['iid']

                if self._skip_item(issue):
                    self.summary.skipped += 1
                    continue

                self.__init_issue_extra_fields(issue)

                issue['notes_data'] = \
                    self.__get_issue_notes(issue_id)
                issue['award_emoji_data'] = \
                    self.__get_award_emoji(GitLabClient.RISSUES, issue_id)

                yield issue

    def __get_issue_notes(self, issue_id):
        """Get issue notes"""

        notes = []

        group_notes = self.client.notes(GitLabClient.RISSUES, issue_id)

        for raw_notes in group_notes:

            for note in json.loads(raw_notes):
                note_id = note['id']
                note['award_emoji_data'] = \
                    self.__get_note_award_emoji(GitLabClient.RISSUES, issue_id, note_id)
                notes.append(note)

        return notes

    def __fetch_merge_requests(self, from_date):
        """Fetch the merge requests."""

        fetch_completed = False
        fetch_from_date = from_date
        last_date = fetch_from_date

        while not fetch_completed:
            try:
                for mr_item in self.__fetch_merge_requests_data(fetch_from_date):
                    last_date = unixtime_to_datetime(self.metadata_updated_on(mr_item))
                    yield mr_item
            except _OutdatedMRsList:
                fetch_from_date = last_date
                logger.debug("MRs list is outdated. Recalculating MR list starting on %s",
                             fetch_from_date)
            else:
                fetch_completed = True

    def __fetch_merge_requests_data(self, from_date):
        merges_groups = self.client.merges(from_date=from_date)

        for raw_merges in merges_groups:
            merges = json.loads(raw_merges)
            for merge in merges:
                merge_id = merge['iid']

                if self._skip_item(merge):
                    self.summary.skipped += 1
                    continue

                # The single merge_request API call returns a more
                # complete merge request, thus we inflate it with
                # other data (e.g., notes, emojis, versions)
                merge_full_raw = self.client.merge(merge_id)
                merge_full = json.loads(merge_full_raw)

                # If during the fetching process a MR is updated,
                # the current process should be canceled because the
                # list of MRs is outdated. It is not ordered from the
                # first updated to the last one.
                updated_on_merge = self.metadata_updated_on(merge)
                updated_on_merge_full = self.metadata_updated_on(merge_full)

                if updated_on_merge != updated_on_merge_full:
                    raise _OutdatedMRsList()

                self.__init_merge_extra_fields(merge_full)

                merge_full['notes_data'] = self.__get_merge_notes(merge_id)
                merge_full['award_emoji_data'] = self.__get_award_emoji(GitLabClient.RMERGES, merge_id)
                merge_full['versions_data'] = self.__get_merge_versions(merge_id)

                yield merge_full

    def __get_merge_notes(self, merge_id):
        """Get merge notes"""

        notes = []

        group_notes = self.client.notes(GitLabClient.RMERGES, merge_id)

        for raw_notes in group_notes:
            for note in json.loads(raw_notes):
                note_id = note['id']
                note['award_emoji_data'] = \
                    self.__get_note_award_emoji(GitLabClient.RMERGES, merge_id, note_id)
                notes.append(note)

        return notes

    def __get_merge_versions(self, merge_id):
        """Get merge versions"""

        versions = []

        group_versions = self.client.merge_versions(merge_id)

        for raw_versions in group_versions:
            for version in json.loads(raw_versions):
                version_id = version['id']
                version_full_raw = self.client.merge_version(merge_id, version_id)
                version_full = json.loads(version_full_raw)

                version_full.pop('diffs', None)
                versions.append(version_full)

        return versions

    def __get_award_emoji(self, item_type, item_id):
        """Get award emojis for issue/merge request"""

        emojis = []

        group_emojis = self.client.emojis(item_type, item_id)
        for raw_emojis in group_emojis:

            for emoji in json.loads(raw_emojis):
                emojis.append(emoji)

        return emojis

    def __get_note_award_emoji(self, item_type, item_id, note_id):
        """Fetch emojis for a note of an issue/merge request"""

        emojis = []

        group_emojis = self.client.note_emojis(item_type, item_id, note_id)
        try:
            for raw_emojis in group_emojis:

                for emoji in json.loads(raw_emojis):
                    emojis.append(emoji)
        except requests.exceptions.HTTPError as error:
            if error.response.status_code == 404:
                logger.warning("Emojis not available for %s ",
                               urijoin(item_type, str(item_id), GitLabClient.RNOTES,
                                       str(note_id), GitLabClient.REMOJI))
                return emojis

        return emojis

    def __init_issue_extra_fields(self, issue):
        """Add fields to an issue"""

        issue['notes_data'] = []
        issue['award_emoji_data'] = []

    def __init_merge_extra_fields(self, merge):
        """Add fields to a merge requests"""

        merge['notes_data'] = []
        merge['award_emoji_data'] = []
        merge['versions_data'] = []


class _OutdatedMRsList(BackendError):
    """Exception raised when the list of MRs is outdated."""

    message = "MRs list is outdated; you should fetch a new one."


class GitLabClient(HttpClient, RateLimitHandler):
    """Client for retieving information from GitLab API

    :param owner: GitLab owner
    :param repository: GitLab owner's repository
    :param token: GitLab auth token to access the API
    :param is_oauth_token: True if the token is OAuth (default False)
    :param base_url: GitLab URL; defaults to https://gitlab.com
     :param sleep_for_rate: sleep until rate limit is reset
     :param min_rate_to_sleep: minimum rate needed to sleep until
          it will be reset
     :param sleep_time: time (in seconds) to sleep in case
        of connection problems
    :param max_retries: number of max retries to a data source
         before raising a RetryError exception
    :param extra_retry_after_status: retry HTTP requests after status
    :param archive: an archive to store/read fetched data
    :param from_archive: it tells whether to write/read the archive
    :param ssl_verify: enable/disable SSL verification
    """
    # API resources
    RISSUES = "issues"
    RMERGES = "merge_requests"
    RNOTES = "notes"
    REMOJI = "award_emoji"
    RPROJECTS = "projects"
    RVERSIONS = "versions"

    # API headers
    HAUTHORIZATION = 'Authorization'
    HPRIVATE_TOKEN = 'PRIVATE-TOKEN'
    HRATE_LIMIT = "RateLimit-Remaining"
    HRATE_LIMIT_RESET = "RateLimit-Reset"

    # Resource parameters
    PSTATE = 'state'
    PORDER_BY = 'order_by'
    PSORT = 'sort'
    PVIEW = 'view'
    PPER_PAGE = 'per_page'
    PUPDATE_AFTER = 'updated_after'

    # Predefined values
    VSTATE_ALL = 'all'
    VORDER_UPDATED_AT = 'updated_at'
    VSORT_ASC = 'asc'
    VVIEW_SIMPLE = 'simple'
    VPER_PAGE = 100

    _users = {}       # users cache

    def __init__(self, owner, repository, token, is_oauth_token=False, base_url=None,
                 sleep_for_rate=False, min_rate_to_sleep=MIN_RATE_LIMIT,
                 sleep_time=DEFAULT_SLEEP_TIME, max_retries=MAX_RETRIES, extra_retry_after_status=None,
                 archive=None, from_archive=False, ssl_verify=True):

        if not token and is_oauth_token:
            raise HttpClientError(cause="is_oauth_token is True but token is None")

        self.owner = owner
        self.repository = repository
        self.token = token
        self.is_oauth_token = is_oauth_token
        self.rate_limit = None
        self.sleep_for_rate = sleep_for_rate

        if base_url:
            parts = urllib.parse.urlparse(base_url)
            base_url = parts.scheme + '://' + parts.netloc + '/api/v4'
        else:
            base_url = GITLAB_API_URL

        super().__init__(base_url, sleep_time=sleep_time, max_retries=max_retries,
                         extra_headers=self._set_extra_headers(), extra_retry_after_status=extra_retry_after_status,
                         archive=archive, from_archive=from_archive, ssl_verify=ssl_verify)
        super().setup_rate_limit_handler(rate_limit_header=self.HRATE_LIMIT,
                                         rate_limit_reset_header=self.HRATE_LIMIT_RESET,
                                         sleep_for_rate=sleep_for_rate,
                                         min_rate_to_sleep=min_rate_to_sleep)

        self._init_rate_limit()

    def issues(self, from_date=None):
        """Get the issues from pagination"""

        payload = {
            self.PSTATE: self.VSTATE_ALL,
            self.PORDER_BY: self.VORDER_UPDATED_AT,
            self.PSORT: self.VSORT_ASC,
            self.PPER_PAGE: self.VPER_PAGE
        }

        if from_date:
            payload[self.PUPDATE_AFTER] = from_date.isoformat()

        return self.fetch_items(self.RISSUES, payload)

    def merges(self, from_date=None):
        """Get the merge requests from pagination"""

        payload = {
            self.PSTATE: self.VSTATE_ALL,
            self.PORDER_BY: self.VORDER_UPDATED_AT,
            self.PSORT: self.VSORT_ASC,
            self.PVIEW: self.VVIEW_SIMPLE,
            self.PPER_PAGE: self.VPER_PAGE
        }

        if from_date:
            payload[self.PUPDATE_AFTER] = from_date.isoformat()

        return self.fetch_items(self.RMERGES, payload)

    def merge(self, merge_id):
        """Get the merge full data"""

        path = urijoin(self.base_url,
                       self.RPROJECTS, urllib.parse.quote(
                           self.owner + '/' + self.repository, safe=''),
                       self.RMERGES, merge_id)

        response = self.fetch(path)

        return response.text

    def merge_versions(self, merge_id):
        """Get the merge versions from pagination"""

        payload = {
            self.PORDER_BY: self.VORDER_UPDATED_AT,
            self.PSORT: self.VSORT_ASC,
            self.PPER_PAGE: self.VPER_PAGE
        }

        path = urijoin(self.RMERGES, str(merge_id), self.RVERSIONS)
        return self.fetch_items(path, payload)

    def merge_version(self, merge_id, version_id):
        """Get merge version detail"""

        path = urijoin(self.base_url,
                       self.RPROJECTS, urllib.parse.quote(
                           self.owner + '/' + self.repository, safe=''),
                       self.RMERGES, merge_id, self.RVERSIONS, version_id)

        response = self.fetch(path)

        return response.text

    def notes(self, item_type, item_id):
        """Get the notes from pagination"""

        payload = {
            self.PORDER_BY: self.VORDER_UPDATED_AT,
            self.PSORT: self.VSORT_ASC,
            self.PPER_PAGE: self.VPER_PAGE
        }

        path = urijoin(item_type, str(item_id), self.RNOTES)

        return self.fetch_items(path, payload)

    def emojis(self, item_type, item_id):
        """Get emojis from pagination"""

        payload = {
            self.PORDER_BY: self.VORDER_UPDATED_AT,
            self.PSORT: self.VSORT_ASC,
            self.PPER_PAGE: self.VPER_PAGE
        }

        path = urijoin(item_type, str(item_id), self.REMOJI)

        return self.fetch_items(path, payload)

    def note_emojis(self, item_type, item_id, note_id):
        """Get emojis of a note"""

        payload = {
            self.PORDER_BY: self.VORDER_UPDATED_AT,
            self.PSORT: self.VSORT_ASC,
            self.PPER_PAGE: self.VPER_PAGE
        }

        path = urijoin(item_type, str(item_id), self.RNOTES,
                       str(note_id), self.REMOJI)

        return self.fetch_items(path, payload)

    def calculate_time_to_reset(self):
        """Calculate the seconds to reset the token requests, by obtaining the different
        between the current date and the next date when the token is fully regenerated.
        """

        time_to_reset = self.rate_limit_reset_ts - (datetime_utcnow().replace(microsecond=0).timestamp() + 1)

        if time_to_reset < 0:
            time_to_reset = 0

        return time_to_reset

    def fetch(self, url, payload=None, headers=None, method=HttpClient.GET, stream=False):
        """Fetch the data from a given URL.

        :param url: link to the resource
        :param payload: payload of the request
        :param headers: headers of the request
        :param method: type of request call (GET or POST)
        :param stream: defer downloading the response body until the response content is available

        :returns a response object
        """
        if not self.from_archive:
            self.sleep_for_rate_limit()

        response = super().fetch(url, payload, headers, method, stream)

        if not self.from_archive:
            self.update_rate_limit(response)

        return response

    def fetch_items(self, path, payload):
        """Return the items from GitLab API using links pagination"""

        page = 0  # current page
        last_page = None  # last page
        url_next = urijoin(
            self.base_url, self.RPROJECTS, urllib.parse.quote(
                self.owner + '/' + self.repository, safe=''), path)

        logger.debug("Get GitLab paginated items from " + url_next)

        response = self.fetch(url_next, payload=payload)
        response.encoding = 'utf-8'

        items = response.text
        page += 1

        if 'last' in response.links:
            last_url = response.links['last']['url']
            last_page = last_url.split('&page=')[1].split('&')[0]
            last_page = int(last_page)
            logger.debug("Page: %i/%i" % (page, last_page))

        while items:
            yield items

            items = None

            if 'next' in response.links:
                url_next = response.links['next']['url']  # Loving requests :)
                response = self.fetch(url_next, payload=payload)
                page += 1

                items = response.text

                if not last_page:
                    logger.debug("Page: %i" % page)
                else:
                    logger.debug("Page: %i/%i" % (page, last_page))

    @staticmethod
    def sanitize_for_archive(url, headers, payload):
        """Sanitize payload of a HTTP request by removing the token information
        before storing/retrieving archived items

        :param: url: HTTP url request
        :param: headers: HTTP headers request
        :param: payload: HTTP payload request

        :returns url, headers and the sanitized payload
        """
        if not headers:
            return url, headers, payload

        if GitLabClient.HPRIVATE_TOKEN in headers:
            headers.pop(GitLabClient.HPRIVATE_TOKEN, None)
        elif GitLabClient.HAUTHORIZATION in headers:
            headers.pop(GitLabClient.HAUTHORIZATION, None)

        return url, headers, payload

    def _set_extra_headers(self):
        """Set extra headers for session"""

        headers = {}

        if not self.token:
            return headers

        if self.is_oauth_token:
            headers = {self.HAUTHORIZATION: "Bearer %s" % self.token}
        else:
            headers = {self.HPRIVATE_TOKEN: self.token}

        return headers

    def _init_rate_limit(self):
        """Initialize rate limit information"""

        url = urijoin(self.base_url, 'projects', urllib.parse.quote(
            self.owner + '/' + self.repository, safe=''))
        try:
            response = super().fetch(url)
            self.update_rate_limit(response)
        except requests.exceptions.HTTPError as error:
            if error.response.status_code == 401:
                raise error
            else:
                logger.warning("Rate limit not initialized: %s", error)


class GitLabCommand(BackendCommand):
    """Class to run GitLab backend from the command line."""

    BACKEND = GitLab

    @classmethod
    def setup_cmd_parser(cls):
        """Returns the GitLab argument parser."""

        parser = BackendCommandArgumentParser(cls.BACKEND,
                                              from_date=True,
                                              token_auth=True,
                                              archive=True,
                                              blacklist=True,
                                              ssl_verify=True)

        # GitLab options
        group = parser.parser.add_argument_group('gitlab arguments')
        group.add_argument('--url', '--enterprise-url', dest='base_url',
                           help="Base URL for GitLab instance")
        group.add_argument('--sleep-for-rate', dest='sleep_for_rate',
                           action='store_true',
                           help="sleep for getting more rate")
        group.add_argument('--min-rate-to-sleep', dest='min_rate_to_sleep',
                           default=MIN_RATE_LIMIT, type=int,
                           help="sleep until reset when the rate limit \
                               reaches this value")
        group.add_argument('--is-oauth-token', dest='is_oauth_token',
                           action='store_true',
                           help="Set when using OAuth2")

        # Generic client options
        group.add_argument('--max-retries', dest='max_retries',
                           default=MAX_RETRIES, type=int,
                           help="number of API call retries")
        group.add_argument('--sleep-time', dest='sleep_time',
                           default=DEFAULT_SLEEP_TIME, type=int,
                           help="sleeping time between API call retries")
        group.add_argument('--extra-retry-status', dest='extra_retry_after_status',
                           default=DEFAULT_RETRY_AFTER_STATUS_CODES, nargs="+", type=int,
                           help="retry HTTP requests after status")

        # Positional arguments
        parser.parser.add_argument('owner',
                                   help="GitLab owner")
        parser.parser.add_argument('repository',
                                   help="GitLab repository")

        return parser
