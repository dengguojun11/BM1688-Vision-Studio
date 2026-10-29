from pathlib import Path


def test_install_service_defaults_to_q4():
    script = Path("scripts/ops/install_locateanything_service.sh").read_text(encoding="utf-8")

    assert 'QUANTIZATION="${LABELU_LA_QUANTIZATION:-q4}"' in script
    assert "Environment=LA_QUANTIZATION=${QUANTIZATION}" in script
    assert "--quantization ${QUANTIZATION}" in script
