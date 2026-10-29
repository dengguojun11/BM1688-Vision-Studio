"""火山方舟 OpenAI 兼容接口：model 字段格式说明见控制台。"""


def normalize_volcengine_ark_model_id(model: str | None) -> str:
    """
    方舟 /chat/completions 请求的 model 应为接入点 ID 或模型名。
    若误写成「volcengine/xxx」会返回 404 InvalidEndpointOrModel.NotFound，此处去掉该前缀。
    """
    m = (model or "").strip()
    prefix = "volcengine/"
    if m.lower().startswith(prefix):
        return m[len(prefix) :].strip()
    return m
