"""Request shapes shared by the local HTTP routes."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictBool


Name = Annotated[str, Field(min_length=1, max_length=128)]
ModID = Annotated[str, Field(min_length=1, max_length=512)]
WorkshopID = Annotated[str, Field(pattern=r"^[0-9]{1,20}$")]
Revision = Annotated[str, Field(min_length=1, max_length=128)]
PathText = Annotated[str, Field(max_length=32767, pattern=r"^[^\x00]*$")]


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Order(RequestBody):
    order: list[ModID] = Field(max_length=4096)


class OrderWrite(Order):
    revision: Revision
    check_dependencies: StrictBool = False


class Profile(RequestBody):
    name: Name


class ProfileSave(Profile):
    note: str = Field(default="", max_length=4096)


class ProfileApply(Profile):
    full_restore: StrictBool = False
    launch: StrictBool = False
    save_revision: Revision | None = None


class BackupRestore(RequestBody):
    name: Annotated[str, Field(min_length=1, max_length=255)]
    confirm: StrictBool


class ShareExport(Order):
    name: Name
    verify: StrictBool = False


class ShareImport(RequestBody):
    code: str = Field(min_length=1, max_length=512 * 1024)


class DependencyPreview(Order):
    share_code: str | None = Field(default=None, min_length=1, max_length=512 * 1024)


class SettingsWrite(RequestBody):
    game_dir: PathText
    save_dir: PathText
    workshop_dir: PathText
    game_version: str = Field(max_length=64)
    downloader_path: PathText
    steam_username: str = Field(max_length=256)
    show_undocumented_config: StrictBool = False
    steam_api_key: str = Field(default="", max_length=512)


class ConfigWrite(RequestBody):
    mod_id: ModID
    id: str = Field(min_length=1, max_length=128)
    revision: Revision
    values: dict[str, Any] | None = None
    text: str | None = Field(default=None, max_length=2 * 1024 * 1024)


class WorkshopOpen(RequestBody):
    id: WorkshopID


class WorkshopDownload(WorkshopOpen):
    confirm_login: StrictBool = False
