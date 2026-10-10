"""One source for store captions and group titles: Макс's region_probe + Ника's overrides."""

from __future__ import annotations

from apprestore_core.region_probe import RegionStatus, label_ru, make_group_titles, make_labels

LABEL_OVERRIDES = {
    RegionStatus.NOT_IN_REGION: "Нет в App Store вашей страны",
    RegionStatus.DELISTED: "Удалено из App Store",
    RegionStatus.UNKNOWN: "Не удалось проверить",
}
GROUP_TITLE_OVERRIDES = {RegionStatus.NOT_IN_REGION: "Нет в App Store вашей страны"}
LABELS = make_labels(LABEL_OVERRIDES)
GROUP_TITLES = make_group_titles(GROUP_TITLE_OVERRIDES)


def caption(status: RegionStatus) -> str:
    return label_ru(status, LABELS) or ""


def region_group_title() -> str:
    return GROUP_TITLES.get(RegionStatus.NOT_IN_REGION) or caption(RegionStatus.NOT_IN_REGION)
