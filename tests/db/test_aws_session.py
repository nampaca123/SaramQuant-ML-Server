import app.db.aws_session as aws_session


def test_uses_saramquant_keys_when_aws_env_absent(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_REGION_NAME", raising=False)
    monkeypatch.setenv("SARAMQUANT_IAM_KEY_ACCESS", "AKIATEST")
    monkeypatch.setenv("SARAMQUANT_IAM_KEY_SECRET", "secret-test")

    session = aws_session.build_session()
    credentials = session.get_credentials()

    assert credentials.access_key == "AKIATEST"
    assert credentials.secret_key == "secret-test"
    assert session.region_name == "ap-northeast-2"


def test_region_override(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("SARAMQUANT_IAM_KEY_ACCESS", "AKIATEST")
    monkeypatch.setenv("SARAMQUANT_IAM_KEY_SECRET", "secret-test")
    monkeypatch.setenv("AWS_REGION_NAME", "us-east-1")

    assert aws_session.build_session().region_name == "us-east-1"


def test_falls_back_to_default_chain_without_saramquant_keys(monkeypatch):
    monkeypatch.delenv("SARAMQUANT_IAM_KEY_ACCESS", raising=False)
    monkeypatch.delenv("SARAMQUANT_IAM_KEY_SECRET", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAROLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "role-secret")

    credentials = aws_session.build_session().get_credentials()

    assert credentials.access_key == "AKIAROLE"
