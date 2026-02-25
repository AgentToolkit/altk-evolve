import type { Entity } from './EntityTable';

interface EntityDetailModalProps {
    entity: Entity;
    onClose: () => void;
}

export default function EntityDetailModal({ entity, onClose }: EntityDetailModalProps) {
    return (
        <div className="modal-backdrop" onClick={onClose}>
            <div
                className="glass-panel modal detail-modal-wide"
                onClick={e => e.stopPropagation()}
            >
                <div className="d-flex align-center justify-between" style={{ marginBottom: "1.5rem" }}>
                    <h3 className="modal-title no-margin">Entity Details</h3>
                    <span className="entity-type">{entity.type}</span>
                </div>

                <div className="detail-field">
                    <label className="detail-field-label">ID</label>
                    <div className="detail-field-value detail-field-value--mono">
                        {entity.id}
                    </div>
                </div>

                <div className="detail-field">
                    <label className="detail-field-label">Content</label>
                    <div className="detail-field-value detail-field-value--pre">
                        {entity.content}
                    </div>
                </div>

                {Object.keys(entity.metadata).length > 0 && (
                    <div className="detail-field">
                        <label className="detail-field-label">Metadata</label>
                        <div className="detail-field-value detail-field-value--mono detail-field-value--pre">
                            {JSON.stringify(entity.metadata, null, 2)}
                        </div>
                    </div>
                )}

                <div className="modal-actions" style={{ marginTop: "2rem" }}>
                    <button type="button" className="btn btn-secondary" onClick={onClose}>Close</button>
                </div>
            </div>
        </div>
    );
}
