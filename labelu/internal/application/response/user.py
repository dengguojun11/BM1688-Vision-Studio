from pydantic import BaseModel, Field

class UserResponse(BaseModel):
    id: int
    username: str
    is_task_superuser: bool = Field(
        default=False,
        description="是否为任务超级用户（管理员），可查看全部任务与标注统计",
    )


class LoginResponse(BaseModel):
    token: str = Field(..., description="description: user credential")


class LogoutResponse(BaseModel):
    msg: str


class AboutResponse(BaseModel):
    version: str = Field(description="后端版本（labelu/version.py）")
    frontend_version: str = Field(
        default="",
        description="前端发版版本（frontend/extensions/FRONTEND_VERSION）",
    )
    user_me_has_superuser_field: bool = Field(
        description="当前安装的 UserResponse 是否包含 is_task_superuser（用于排查发版是否生效）",
    )
    task_superuser_usernames_configured: bool = Field(
        description="TASK_SUPERUSER_USERNAMES 是否在 .env 中配置了非空值",
    )
