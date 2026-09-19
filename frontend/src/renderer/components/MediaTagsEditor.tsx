import type { MediaAssetDetail } from "../lib/types";
import { useI18n } from "../i18n";

export default function MediaTagsEditor({ asset, onSearch }: {
  asset: MediaAssetDetail;
  onSearch(query: string): void;
}) {
  const { t } = useI18n();
  const tags = asset.structuredTags ?? [];

  return <details className="library-tag-editor">
    <summary>{t("media.tagsTitle")}</summary>
    <div className="library-tag-content">
    <div className="library-tag-list">
      {tags.map((tag) => <button type="button" key={tag.id} title={`${tag.origin} · ${tag.evidence} · ${Math.round(tag.confidence * 100)}%`}
        onClick={() => onSearch(`${tag.category}:${JSON.stringify(tag.value)}`)}>
        {tag.category}:{tag.value} <small>{t(`media.tagOrigin.${tag.origin}`)}</small>
      </button>)}
    </div>
    </div>
  </details>;
}
