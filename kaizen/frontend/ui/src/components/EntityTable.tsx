import { Eye, Trash2 } from 'lucide-react';

interface Entity {
    id: string;
    type: string;
    content: string;
    created_at?: string;
    metadata: Record<string, any>;
}

interface EntityTableProps {
    entities: Entity[];
    onView: (entity: Entity) => void;
    onDelete: (entityId: string) => void;
}

export default function EntityTable({ entities, onView, onDelete }: EntityTableProps) {
    return (
        <div className="glass-panel table-container">
            <table className="data-table">
                <thead>
                    <tr>
                        <th style={{ width: '150px' }}>Type</th>
                        <th>Content</th>
                        <th>Created</th>
                        <th className="text-right">Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {entities.length === 0 ? (
                        <tr>
                            <td colSpan={4} className="text-center py-8 text-secondary">
                                No entities found in this namespace.
                            </td>
                        </tr>
                    ) : (
                        entities.map(ent => (
                            <tr key={ent.id}>
                                <td>
                                    <span className="badge">{ent.type}</span>
                                </td>
                                <td>
                                    <div style={{ fontSize: "0.95rem", color: "#CBD5E1" }}>
                                        {ent.content.length > 100 ? `${ent.content.substring(0, 100)}...` : ent.content}
                                    </div>
                                </td>
                                <td className="text-secondary small-text">
                                    {ent.created_at ? new Date(ent.created_at).toLocaleString() : 'N/A'}
                                </td>
                                <td className="text-right d-flex justify-end gap-2" style={{ border: 'none' }}>
                                    <button
                                        className="btn-icon"
                                        title="View Details"
                                        onClick={() => onView(ent)}
                                    >
                                        <Eye size={18} />
                                    </button>
                                    <button
                                        className="btn-icon text-danger"
                                        title="Delete Entity"
                                        onClick={() => onDelete(ent.id)}
                                    >
                                        <Trash2 size={18} />
                                    </button>
                                </td>
                            </tr>
                        ))
                    )}
                </tbody>
            </table>
        </div>
    );
}

export type { Entity };
