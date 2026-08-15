'''Regression guards for shared intrusion and parking crime triggers.'''

from app.services.crime_classifier import CrimeClassifier


def _classifier() -> CrimeClassifier:
    classifier = CrimeClassifier()
    classifier._enabled = True
    classifier._trigger_classes = {'person'}
    classifier._trigger_on_parking = True
    classifier._cooldown_map.clear()
    return classifier


def test_parking_person_activity_can_trigger_without_intrusion():
    classifier = _classifier()
    assert classifier.should_classify(
        'parking-camera',
        1,
        'person',
        has_intrusion=False,
        parking_activity=True,
    )


def test_perimeter_intrusion_trigger_is_unchanged():
    classifier = _classifier()
    assert classifier.should_classify(
        'perimeter-camera',
        10,
        'person',
        has_intrusion=True,
    )
    assert not classifier.should_classify(
        'perimeter-camera',
        11,
        'car',
        has_intrusion=True,
    )
    assert not classifier.should_classify(
        'perimeter-camera',
        12,
        'person',
        has_intrusion=False,
    )
