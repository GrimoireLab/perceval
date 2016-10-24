# -*- coding: utf-8 -*-
#
# Copyright (C) 2016 Bitergia
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
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors:
#     Alberto Martín <alberto.martin@bitergia.com>
#     Santiago Dueñas <sduenas@bitergia.com>
#

import json
import logging
import os.path
import time

import requests

from ..backend import Backend, BackendCommand, metadata
from ..cache import Cache
from ..errors import CacheError
from ..utils import (DEFAULT_DATETIME,
                     datetime_to_utc,
                     str_to_datetime,
                     urljoin)

MAX_QUESTIONS = 100  # Maximum number of reviews per query

logger = logging.getLogger(__name__)


class StackExchange(Backend):
    """StackExchange backend for Perceval.

    This class retrieves the questions stored in any of the
    StackExchange sites. To initialize this class the
    site must be provided.

    :param site: StackExchange site
    :param tagged: filter items by question Tag
    :param token: StackExchange access_token for the API
    :param tag: label used to mark the data
    :param cache: cache object to store raw data
    """
    version = '0.4.0'

    def __init__(self, site, tagged=None, token=None,
                 max_questions=None, tag=None, cache=None):
        origin = site

        super().__init__(origin, tag=tag, cache=cache)
        self.site = site
        self.tagged = tagged
        self.max_questions = max_questions
        self.client = StackExchangeClient(site, tagged, token, max_questions)

    @metadata
    def fetch(self, from_date=DEFAULT_DATETIME):
        """Fetch the questions from the site.

        The method retrieves, from a StackExchange site, the
        questions updated since the given date.

        :param from_date: obtain questions updated since this date

        :returns: a generator of questions
        """
        if not from_date:
            from_date = DEFAULT_DATETIME

        logger.info("Looking for questions at site '%s', with tag '%s' and updated from '%s'",
                    self.site, self.tagged, str(from_date))

        self._purge_cache_queue()

        from_date = datetime_to_utc(from_date)

        whole_pages = self.client.get_questions(from_date)

        for whole_page in whole_pages:
            self._push_cache_queue(whole_page)
            self._flush_cache_queue()
            questions = self.parse_questions(whole_page)
            for question in questions:
                yield question

    @metadata
    def fetch_from_cache(self):
        """Fetch the questions from the cache.

        :returns: a generator of questions

        :raises CacheError: raised when an error occurs accessing the
            cache
        """
        if not self.cache:
            raise CacheError(cause="cache instance was not provided")

        cache_items = self.cache.retrieve()

        for items in cache_items:
            questions = self.parse_questions(items)
            for question in questions:
                yield question

    @staticmethod
    def metadata_id(item):
        """Extracts the identifier from a StackExchange item."""

        return str(item['question_id'])

    @staticmethod
    def metadata_updated_on(item):
        """Extracts the update time from a StackExchange item.

        The timestamp is extracted from 'last_activity_date' field.
        This date is a UNIX timestamp but needs to be converted to
        a float value.

        :param item: item generated by the backend

        :returns: a UNIX timestamp
        """
        return float(item['last_activity_date'])

    @staticmethod
    def metadata_category(item):
        """Extracts the category from a StackExchange item.

        This backend only generates one type of item which is
        'question'.
        """
        return 'question'

    @staticmethod
    def parse_questions(raw_page):
        """Parse a StackExchange API raw response.

        The method parses the API response retrieving the
        questions from the received items

        :param items: items from where to parse the questions

        :returns: a generator of questions
        """
        raw_questions = json.loads(raw_page)
        questions = raw_questions['items']
        for question in questions:
            yield question


class StackExchangeClient:
    """StackExchange API client.

    This class implements a simple client to retrieve questions from
    any Stackexchange site.

    :param site: URL of the Bugzilla server
    :param tagged: filter items by question Tag
    :param token: StackExchange access_token for the API
    :param max_questions: max number of questions per query

    :raises HTTPError: when an error occurs doing the request
    """
    # Filters are immutable and non-expiring. This filter allows to retrieve all
    # the information regarding Each question. To know more, visit
    # https://api.stackexchange.com/docs/questions and paste the filter in the
    # whitebox filter. It will display a list of checkboxes with the selected
    # values for the filter provided.

    QUESTIONS_FILTER = 'Bf*y*ByQD_upZqozgU6lXL_62USGOoV3)MFNgiHqHpmO_Y-jHR'
    STACKEXCHANGE_API_URL = 'https://api.stackexchange.com'
    VERSION_API = '2.2'

    def __init__(self, site, tagged, token, max_questions):
        self.site = site
        self.tagged = tagged
        self.token = token
        self.max_questions = max_questions

    def __build_base_url(self, type='questions'):
        base_api_url = self.STACKEXCHANGE_API_URL
        base_api_url = urljoin(base_api_url, self.VERSION_API, type)
        return base_api_url

    def __build_payload(self, page, from_date, order='desc', sort='activity'):
        payload = {'page': page,
                   'pagesize': self.max_questions,
                   'order': order,
                   'sort': sort,
                   'tagged': self.tagged,
                   'site': self.site,
                   'key': self.token,
                   'filter': self.QUESTIONS_FILTER}
        if from_date:
            timestamp = int(from_date.timestamp())
            payload['min'] = timestamp
        return payload

    def __log_status(self, quota_remaining, quota_max, page_size, total):

        logger.debug("Rate limit: %s/%s" % (quota_remaining,
                                            quota_max))
        if (total != 0):
            nquestions = min(page_size, total)
            logger.info("Fetching questions: %s/%s" % (nquestions,
                                                       total))
        else:
            logger.info("No questions were found.")

    def get_questions(self, from_date):
        """Retrieve all the questions from a given date.

        :param from_date: obtain questions updated since this date
        """

        page = 1
        req = requests.get(self.__build_base_url(),
                           params=self.__build_payload(page, from_date))
        req.raise_for_status()
        questions = req.text

        data = req.json()
        tquestions = data['total']
        nquestions = data['page_size']

        self.__log_status(data['quota_remaining'],
                          data['quota_max'],
                          nquestions,
                          tquestions)

        while questions:
            yield questions
            questions = None

            if data['has_more']:
                page += 1

                backoff = data.get('backoff', None)
                if backoff:
                    logger.debug("Expensive query. Wait %s secs to send a new request",
                                 backoff)
                    time.sleep(float(backoff))

                req = requests.get(self.__build_base_url(),
                                   params=self.__build_payload(page, from_date))
                req.raise_for_status()
                data = req.json()
                questions = req.text
                nquestions += data['page_size']
                self.__log_status(data['quota_remaining'],
                                  data['quota_max'],
                                  nquestions,
                                  tquestions)


class StackExchangeCommand(BackendCommand):
    """Class to run StackExchange backend from the command line."""

    def __init__(self, *args):
        super().__init__(*args)
        self.site = self.parsed_args.site
        self.tagged = self.parsed_args.tagged
        self.token = self.parsed_args.token
        self.max_questions = self.parsed_args.max_questions
        self.from_date = str_to_datetime(self.parsed_args.from_date)
        self.tag = self.parsed_args.tag
        self.outfile = self.parsed_args.outfile

        if not self.parsed_args.no_cache:
            if not self.parsed_args.cache_path:
                base_path = os.path.expanduser('~')
                base_path = os.path.join(base_path, '.perceval', 'cache')
            else:
                base_path = self.parsed_args.cache_path

            cache_path = os.path.join(base_path, self.site)

            cache = Cache(cache_path)

            if self.parsed_args.clean_cache:
                cache.clean()
            else:
                cache.backup()
        else:
            cache = None

        self.backend = StackExchange(self.site,
                                     tagged=self.tagged,
                                     token=self.token,
                                     max_questions=self.max_questions,
                                     tag=self.tag,
                                     cache=cache)

    def run(self):
        """Fetch and print the Questions.

        This method runs the backend to fetch the Questions (plus all
        its answers and comments) of a given site and tag.
        Questions are converted to JSON objects and printed to the
        defined output.
        """
        if self.parsed_args.fetch_cache:
            questions = self.backend.fetch_from_cache()
        else:
            questions = self.backend.fetch(from_date=self.from_date)

        try:
            for question in questions:
                obj = json.dumps(question, indent=4, sort_keys=True)
                self.outfile.write(obj)
                self.outfile.write('\n')
        except requests.exceptions.HTTPError as e:
            raise requests.exceptions.HTTPError(str(e.response.json()))
        except IOError as e:
            raise RuntimeError(str(e))
        except Exception as e:
            if self.backend.cache:
                self.backend.cache.recover()
            raise RuntimeError(str(e))

    @classmethod
    def create_argument_parser(cls):
        """Returns the StackExchange argument parser."""

        parser = super().create_argument_parser()

        # StackExchange options
        group = parser.add_argument_group('StackExchange arguments')

        group.add_argument("--site", required=True,
                           help="StackExchange site")
        group.add_argument("--tagged",
                           help="filter items by question Tag")
        group.add_argument("--token",
                           help="StackExchange token for the API")
        group.add_argument('--max-questions', dest='max_questions',
                           type=int, default=MAX_QUESTIONS,
                           help="Maximum number of questions requested in the same query")

        return parser
