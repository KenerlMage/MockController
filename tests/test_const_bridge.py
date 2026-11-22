"""Controller 侧：字段映射反向换算（SR02-AR09 可逆）。"""
import pytest


def _set(db, mappings, pn='SPM-RH-01', key='SPM_RH'):
    import const_catalog

    return const_catalog.set_field_mapping(
        {'pn': pn, 'model_key': key, 'product': '1000-A', 'subsystem': 'SPM', 'mappings': mappings},
        db_path=db,
    )


def _apply(db, rows, reverse=False, pn='SPM-RH-01', key='SPM_RH'):
    import const_catalog

    return const_catalog.apply_field_mapping(pn, key, rows, db_path=db, reverse=reverse)


def test_reverse_roundtrip(tmp_path):
    import ate_db

    db = tmp_path / 'c.db'
    ate_db.init_db(db)
    saved = _set(db, [{'source_field': 'gain_raw', 'target_field': 'gain_offset',
                       'scale': 2.0, 'offset': -1.0, 'required': True}])
    assert saved['success'], saved

    forward = _apply(db, [{'source_field': 'gain_raw', 'value': 12.5}])
    assert forward['values']['gain_offset'] == pytest.approx(24.0)

    back = _apply(db, [{'source_field': 'gain_raw', 'value': 24.0}], reverse=True)
    assert back['values']['gain_offset'] == pytest.approx(12.5)
    assert back['reverse'] is True


def test_reverse_rejects_zero_scale(tmp_path):
    import ate_db

    db = tmp_path / 'c2.db'
    ate_db.init_db(db)
    _set(db, [{'source_field': 'a', 'target_field': 'b', 'scale': 0, 'offset': 0, 'required': True}],
         pn='P1', key='M1')
    with pytest.raises(ValueError) as exc:
        _apply(db, [{'source_field': 'a', 'value': 1}], reverse=True, pn='P1', key='M1')
    assert 'scale' in str(exc.value)


def test_forward_unaffected(tmp_path):
    """加了 reverse 分支后，默认方向仍是正向换算。"""
    import ate_db

    db = tmp_path / 'c3.db'
    ate_db.init_db(db)
    _set(db, [{'source_field': 'a', 'target_field': 'b', 'scale': 3.0, 'offset': 0.5,
               'required': True}], pn='P2', key='M2')
    out = _apply(db, [{'source_field': 'a', 'value': 2}], pn='P2', key='M2')
    assert out['values']['b'] == pytest.approx(6.5)
    assert out['reverse'] is False
