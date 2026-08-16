from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.desktop_assets import asset_path, cache_control, media_type, validate_filename

router = APIRouter(tags=["desktop"])


@router.get("/desktop/{filename}")
def download_desktop_asset(filename: str):
    try:
        validate_filename(filename)
        path = asset_path(filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if path is None:
        raise HTTPException(status_code=404, detail="Not found")
    inline = filename.lower().endswith(".xml")
    return FileResponse(
        path,
        media_type=media_type(filename),
        filename=filename,
        content_disposition_type="inline" if inline else "attachment",
        headers={"Cache-Control": cache_control(filename)},
    )
