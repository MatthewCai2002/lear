# Copyright © 2019 Province of British Columbia
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests to assure the shadow filing Model.

Test-Suite to ensure that the Business Model is working as expected.
"""
import copy
import datetime
import json
from http import HTTPStatus

import datedelta
import pytest
from flask import current_app
from freezegun import freeze_time
from registry_schemas.example_data import (
    ANNUAL_REPORT,
    CHANGE_OF_DIRECTORS,
    FILING_HEADER,
    SPECIAL_RESOLUTION,
)
from sqlalchemy_continuum import versioning_manager

from legal_api.exceptions import BusinessException
from legal_api.models.shadow_filing import ShadowFiling
from legal_api.models import User
from tests.unit.models import (
    factory_business,
    factory_shadow_filing,
    factory_user,
)


def test_minimal_shadow_filing_json(session):
    """Assert that a minimal filing can be created."""
    b = factory_business('CP1234567')

    data = {'filing': 'not a real filing, fail validation'}

    filing = ShadowFiling()
    filing.business_id = b.id
    filing.filing_date = datetime.datetime.utcnow()
    filing.filing_data = json.dumps(data)
    filing.save()

    assert filing.source == ShadowFiling.Source.LEAR.value
    assert filing.id is not None


def test_shadow_filing_json(session):
    """Assert that an AR filing can be saved."""
    import copy
    b = factory_business('CP1234567')
    filing = factory_shadow_filing(b, ANNUAL_REPORT)

    ar = copy.deepcopy(ANNUAL_REPORT)
    ar['filing']['header']['filingId'] = filing.id
    ar['filing']['header']['colinIds'] = []

    assert filing.id
    assert filing.json['filing']['business'] == ANNUAL_REPORT['filing']['business']
    assert filing.json['filing']['annualReport'] == ANNUAL_REPORT['filing']['annualReport']


def test_shadow_filing_missing_name(session):
    """Assert that an AR filing can be saved."""
    import copy
    identifier = 'CP7654321'
    b = factory_business(identifier)
    ar = copy.deepcopy(ANNUAL_REPORT)
    ar['filing']['header'].pop('name', None)

    with pytest.raises(BusinessException) as excinfo:
        factory_shadow_filing(b, ar)

    assert excinfo.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY
    assert excinfo.value.error == 'No filings found.'


def test_shadow_filing_dump_json(session):
    """Assert the filing json serialization works correctly."""
    import copy
    identifier = 'CP7654321'
    b = factory_business(identifier)

    # Check base JSON
    filings = factory_shadow_filing(b, ANNUAL_REPORT)

    assert filings.json['filing']['business'] == ANNUAL_REPORT['filing']['business']
    assert filings.json['filing']['annualReport'] == ANNUAL_REPORT['filing']['annualReport']

    # Check payment token
    ar = copy.deepcopy(ANNUAL_REPORT)
    token = 'token'
    ar['filing']['header']['paymentToken'] = token
    filings = factory_shadow_filing(b, ar)
    assert filings.json['filing']['header']['paymentToken'] == token

    # check submitter
    u = User()
    u.username = 'submitter'
    u.save()
    ar = copy.deepcopy(ANNUAL_REPORT)
    filings = factory_shadow_filing(b, ar)
    filings.submitter_id = u.id
    filings.save()
    assert filings.json['filing']['header']['submitter'] == u.username

    # check Exception
    ar = copy.deepcopy(ANNUAL_REPORT)
    filings = factory_shadow_filing(b, ar)
    filings.save()
    filings.submitter_id = -1  # some bogus id to throw an error
    with pytest.raises(KeyError):
        filings.json()


def test_shadow_filing_save_to_session(session):
    """Assert that the filing is saved to the session but not committed."""
    from sqlalchemy.orm.session import Session

    filing = ShadowFiling()

    assert not session.new
    assert not Session.object_session(filing)

    filing.save_to_session()

    assert filing.id is None
    assert session.new
    assert Session.object_session(filing)


def test_get_shadow_filings_by_status(session):
    """Assert that a filing can be retrieved by status."""
    business = factory_business('CP1234567')
    filing = ShadowFiling()
    filing.business_id = business.id
    filing.filing_json = ANNUAL_REPORT
    filing.save()

    rv = ShadowFiling.get_filings_by_status(business.id, [ShadowFiling.Status.COMPLETED.value])

    assert rv
    assert rv[0].status == ShadowFiling.Status.COMPLETED.value


def test_get_shadow_filings_by_status__default_order(session):
    """Assert that a filing can be retrieved.

    by status and is returned in the default order.
    default order is submission_date, and then effective_date.
    """
    # setup
    base_filing = copy.deepcopy(FILING_HEADER)
    base_filing['specialResolution'] = SPECIAL_RESOLUTION
    uow = versioning_manager.unit_of_work(session)
    business = factory_business('CP1234567')

    completion_date = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc)

    # setup - create multiple filings on the same day & time
    filing_ids = []
    file_counter = -1
    with freeze_time(completion_date):
        for i in range(0, 5):
            effective_date = f'200{i}-04-15T00:00:00+00:00'

            base_filing['filing']['header']['effectiveDate'] = effective_date
            filing = ShadowFiling()
            filing._filing_date = completion_date
            filing.business_id = business.id
            filing.filing_json = base_filing
            filing.effective_date = datetime.datetime.fromisoformat(effective_date)
            filing.save()

            filing_ids.append(filing.id)
            file_counter += 1

    # test
    rv = ShadowFiling.get_filings_by_status(business.id, [ShadowFiling.Status.COMPLETED.value])

    # check
    assert rv
    # filings should be in newest to oldest effective date order
    for filing in rv:
        assert filing.id == filing_ids[file_counter]
        file_counter -= 1


def test_get_most_recent_shadow_filing_by_legal_type_in_json(session):
    """Assert that the most recent legal filing can be retrieved."""
    business = factory_business('CP1234567')

    for i in range(1, 5):
        effective_date = f'200{i}-07-01T00:00:00+00:00'
        completion_date = datetime.datetime.fromisoformat(effective_date)

        base_filing = copy.deepcopy(ANNUAL_REPORT)
        cod = copy.deepcopy(CHANGE_OF_DIRECTORS)
        base_filing['filing']['changeOfDirectors'] = cod

        base_filing['filing']['header']['effectiveDate'] = effective_date
        filing = ShadowFiling()
        filing._filing_date = completion_date
        filing.business_id = business.id
        filing.filing_json = base_filing
        filing.effective_date = datetime.datetime.fromisoformat(effective_date)
        filing.save()

    f = ShadowFiling.get_most_recent_legal_filing(business.id, 'changeOfDirectors')
    assert f.effective_date == datetime.datetime.fromisoformat(effective_date)
    assert f.filing_type == 'annualReport'
    assert f.id == filing.id


def test_get_most_recent_shadow_filing_by_legal_type_db_field(session):
    """Assert that the most recent legal filing can be retrieved.

    Create 3 filings, find the 2 one by the type only.
    """
    business = factory_business('CP1234567')

    # filing 1
    effective_date = '2001-07-01T00:00:00+00:00'
    completion_date = datetime.datetime.fromisoformat(effective_date)
    base_filing = copy.deepcopy(ANNUAL_REPORT)
    base_filing['filing']['header']['effectiveDate'] = effective_date
    filing1 = ShadowFiling()
    filing1._filing_date = completion_date
    filing1.business_id = business.id
    filing1.filing_json = base_filing
    filing1.effective_date = datetime.datetime.fromisoformat(effective_date)
    filing1.save()

    # filing 2 <- target
    effective_date = '2002-07-01T00:00:00+00:00'
    completion_date = datetime.datetime.fromisoformat(effective_date)
    base_filing = copy.deepcopy(FILING_HEADER)
    base_filing['filing']['header']['effectiveDate'] = effective_date
    base_filing['filing']['header']['name'] = 'changeOfDirectors'
    base_filing['filing']['header']['availableOnPaperOnly'] = True
    filing2 = ShadowFiling()
    filing2._filing_date = completion_date
    filing2.business_id = business.id
    filing2.filing_json = base_filing
    filing2.effective_date = datetime.datetime.fromisoformat(effective_date)
    filing2.save()

    # filing 3
    effective_date = '2003-07-01T00:00:00+00:00'
    completion_date = datetime.datetime.fromisoformat(effective_date)
    base_filing = copy.deepcopy(ANNUAL_REPORT)
    base_filing['filing']['header']['effectiveDate'] = effective_date
    filing3 = ShadowFiling()
    filing3._filing_date = completion_date
    filing3.business_id = business.id
    filing3.filing_json = base_filing
    filing3.effective_date = datetime.datetime.fromisoformat(effective_date)
    filing3.save()

    f = ShadowFiling.get_most_recent_legal_filing(business.id, 'changeOfDirectors')
    assert f.filing_type == 'changeOfDirectors'
    assert f.id == filing2.id


# testdata pattern is ({str: environment}, {expected return value})
TEST_FILING_GO_LIVE_DATE = [
    ('filing returned', 10, 'rv is not None', ShadowFiling.Status.COMPLETED.value),
    ('no filing returned', -10, 'rv == []', None),
]


@pytest.mark.parametrize('test_type,days,expected,status', TEST_FILING_GO_LIVE_DATE)
def test_get_shadow_filings_by_status_before_go_live_date(session, test_type, days, expected, status):
    """Assert that a filing can be retrieved by status."""
    import copy
    business = factory_business('CP1234567')
    ar = copy.deepcopy(ANNUAL_REPORT)

    go_live_date = datetime.date.fromisoformat(current_app.config.get('GO_LIVE_DATE'))
    filing_date = go_live_date + datetime.timedelta(days=days)

    filing = ShadowFiling()
    filing.filing_date = filing_date
    filing.business_id = business.id
    filing.filing_json = ar
    filing.save()

    rv = ShadowFiling.get_filings_by_status(business.id, [ShadowFiling.Status.COMPLETED.value], go_live_date)

    assert eval(expected)  # pylint: disable=eval-used; useful for parameterized tests
    if rv:
        assert rv[0].status == status


def test_get_internal_shadow_filings(session, client, jwt):
    """Assert that the get_completed_filings_for_colin returns completed filings with no colin ids set."""
    from legal_api.models import ShadowFiling
    from legal_api.models.legacy_outputs import LegacyOutputs
    from tests.unit.models import factory_completed_shadow_filing
    # setup
    identifier = 'CP7654321'
    b = factory_business(identifier)
    filing = factory_completed_shadow_filing(b, ANNUAL_REPORT)
    assert filing.status == ShadowFiling.Status.COMPLETED.value
    colin_event_id = LegacyOutputs()
    colin_event_id.colin_event_id = 12346
    filing.colin_event_ids.append(colin_event_id)
    filing.save()
    filings = ShadowFiling.get_completed_filings_for_colin()

    # test method
    # assert doesn't return completed filing with colin_event_ids set
    assert len(filings) == 0
    # assert returns completed filings with colin_event_id not set
    filing.colin_event_ids.clear()
    filing.save()
    filings = ShadowFiling.get_completed_filings_for_colin()
    assert len(filings) == 1
    assert filing.id == filings[0].json['filing']['header']['filingId']
    assert filings[0].json['filing']['header']['colinIds'] == []
    # assert doesn't return non completed filings
    filing.transaction_id = None
    filing.save()
    assert filing.status != ShadowFiling.Status.COMPLETED.value
    filings = ShadowFiling.get_completed_filings_for_colin()
    assert len(filings) == 0


def test_get_a_businesses_most_recent_shadow_filing_of_a_type(session):
    """Assert that the most recent completed filing of a specified type is returned."""
    from legal_api.models import ShadowFiling
    from tests.unit.models import factory_completed_shadow_filing
    # setup
    identifier = 'CP7654321'
    b = factory_business(identifier)
    ar = copy.deepcopy(ANNUAL_REPORT)
    base_ar_date = datetime.datetime(2001, 8, 5, 7, 7, 58, 272362, tzinfo=datetime.timezone.utc)
    filings = []
    for i in range(0, 5):
        filing_date = base_ar_date + datedelta.datedelta(years=i)
        ar['filing']['annualReport']['annualGeneralMeetingDate'] = \
            filing_date.date().isoformat()
        filing = factory_completed_shadow_filing(b, ar, filing_date)
        filings.append(filing)
    # test
    filing = ShadowFiling.get_a_businesses_most_recent_filing_of_a_type(b.id, ShadowFiling.FILINGS['annualReport']['name'])

    # assert that we get the last filing
    assert filings[4] == filing


def test_save_shadow_filing_with_colin_id(session):
    """Assert that saving a filing from the coops-updater-job user is set to paid and source is colin."""
    from legal_api.models import ShadowFiling
    # setup
    filing = ShadowFiling()
    filing.filing_json = ANNUAL_REPORT
    filing.source = ShadowFiling.Source.COLIN.value
    filing.save()

    # test
    assert filing.source == ShadowFiling.Source.COLIN.value
    assert filing.status == ShadowFiling.Status.PAID.value


def test_save_shadow_filing_colin_only(session):
    """Assert that the in colin only flag is retrieved and saved."""
    from legal_api.models import ShadowFiling
    # setup
    filing = ShadowFiling()
    filing.filing_json = FILING_HEADER
    filing.save()

    # test
    assert filing.json['filing']['header']['inColinOnly'] is False
    assert filing.colin_only == False


def test_submitter_info(session):
    user = factory_user('idir/staff-person')
    filing = ShadowFiling()
    filing.submitter_roles = 'STAFF'
    filing.submitter_id = user.id
    filing.save()

    assert filing.id
 