import pytest


@pytest.mark.parametrize('path', ['/', '/candidate/profile', '/employer/candidates'])
def test_frontend_entry_is_not_cached_across_releases(path, clients, monkeypatch, tmp_path):
    import backend.main as main
    (tmp_path / 'index.html').write_text('<html>current frontend</html>', encoding='utf-8')
    monkeypatch.setattr(main, 'dist', tmp_path)
    response = clients[0].get(path)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['content-type'].startswith('text/html')
    assert 'current frontend' in response.text
