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


def test_v4_vit_checkpoint_keys_are_remapped_to_v5_layout():
    from app.services.crime_classifier import _adapt_hf_vit_state_dict

    ckpt = {
        'vit.embeddings.cls_token': 0,
        'vit.encoder.layer.0.attention.attention.query.weight': 1,
        'vit.encoder.layer.0.attention.output.dense.bias': 2,
        'vit.encoder.layer.0.intermediate.dense.weight': 3,
        'vit.encoder.layer.0.output.dense.weight': 4,
        'classifier.weight': 5,
    }
    target = {
        'vit.embeddings.cls_token',
        'vit.layers.0.attention.q_proj.weight',
        'vit.layers.0.attention.o_proj.bias',
        'vit.layers.0.mlp.fc1.weight',
        'vit.layers.0.mlp.fc2.weight',
        'classifier.weight',
    }
    adapted = _adapt_hf_vit_state_dict(ckpt, target)
    assert set(adapted) == target
    assert adapted['vit.layers.0.attention.o_proj.bias'] == 2
    assert adapted['vit.layers.0.mlp.fc2.weight'] == 4
    # Already-matching checkpoints pass through untouched.
    assert _adapt_hf_vit_state_dict({'classifier.weight': 5}, target) == {'classifier.weight': 5}
