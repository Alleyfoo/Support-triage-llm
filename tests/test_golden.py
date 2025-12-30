import json
import os
import sys

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.knowledge import load_knowledge
from app.pipeline import run_pipeline


def test_test_emails_cover_all_keys():
    data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    dataset_path = os.path.join(data_dir, 'test_emails.json')
    with open(dataset_path, encoding='utf-8') as fh:
        emails = json.load(fh)

    assert len(emails) == 10
    knowledge = load_knowledge()

    for email in emails:
        metadata = {'expected_keys': email['expected_keys']}
        result = run_pipeline(email['body'], metadata=metadata)
        assert result['evaluation']['score'] == pytest.approx(1.0)
        for key in email['expected_keys']:
            assert knowledge[key].split()[0].lower() in result['reply'].lower()
