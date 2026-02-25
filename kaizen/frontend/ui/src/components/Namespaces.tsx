import { useState } from 'react';
import { Folder, Trash2, Plus, AlertCircle, RefreshCw, Eye } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useApi } from '../hooks/useApi';
import ConfirmDialog from './ConfirmDialog';

interface Namespace {
    id: string;
    amount_of_entities: number;
}

export default function Namespaces() {
    const { data: namespaces, loading, error, refetch } = useApi<Namespace[]>('/api/namespaces');

    // Create Modal State
    const [isCreateOpen, setIsCreateOpen] = useState(false);
    const [newName, setNewName] = useState("");
    const [createError, setCreateError] = useState<string | null>(null);

    // Delete confirmation state
    const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
    const [errorToast, setErrorToast] = useState<string | null>(null);

    const handleCreate = async (e: React.FormEvent) => {
        e.preventDefault();
        setCreateError(null);
        if (!newName.trim()) {
            setCreateError("Namespace name cannot be empty");
            return;
        }

        try {
            const res = await fetch('/api/namespaces', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ namespace_id: newName.trim() })
            });

            if (!res.ok) {
                const d = await res.json();
                throw new Error(d.detail || 'Failed to create namespace');
            }

            setNewName("");
            setIsCreateOpen(false);
            refetch();
        } catch (err: any) {
            setCreateError(err.message);
        }
    };

    const handleDelete = async (id: string) => {
        try {
            const res = await fetch(`/api/namespaces/${encodeURIComponent(id)}`, {
                method: 'DELETE'
            });
            if (!res.ok) {
                const d = await res.json();
                throw new Error(d.detail || 'Failed to delete namespace');
            }
            refetch();
        } catch (err: any) {
            setErrorToast(err.message);
            setTimeout(() => setErrorToast(null), 5000);
        } finally {
            setDeleteTarget(null);
        }
    };

    return (
        <div className="namespaces-container">
            <div className="page-header">
                <div>
                    <h2 className="section-title">Namespaces</h2>
                    <p className="text-secondary">Manage separated knowledge bases for your agents</p>
                </div>
                <button className="btn btn-primary" onClick={() => setIsCreateOpen(true)}>
                    <Plus size={18} />
                    Create Namespace
                </button>
            </div>

            {error ? (
                <div className="error-state">
                    <AlertCircle size={40} />
                    <p>{error}</p>
                    <button className="btn btn-secondary" onClick={refetch}>
                        <RefreshCw size={16} /> Retry
                    </button>
                </div>
            ) : loading ? (
                <div className="loading-state">
                    <div className="loader" data-testid="loader"></div>
                </div>
            ) : (
                <div className="glass-panel table-container">
                    <table className="data-table">
                        <thead>
                            <tr>
                                <th>ID Name</th>
                                <th>Entities</th>
                                <th className="text-right">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {(namespaces || []).length === 0 ? (
                                <tr>
                                    <td colSpan={3} className="text-center py-8 text-secondary">
                                        No namespaces found. Create one to get started.
                                    </td>
                                </tr>
                            ) : (
                                (namespaces || []).map(ns => (
                                    <tr key={ns.id}>
                                        <td>
                                            <div className="d-flex align-center gap-2 font-medium">
                                                <Folder size={18} className="text-primary" />
                                                {ns.id}
                                            </div>
                                        </td>
                                        <td>
                                            <span className="badge">{ns.amount_of_entities}</span>
                                        </td>
                                        <td className="text-right d-flex justify-end gap-2 table-actions">
                                            <Link
                                                to={`/namespaces/${ns.id}/entities`}
                                                className="btn-icon"
                                                title="View Entities"
                                            >
                                                <Eye size={18} />
                                            </Link>
                                            <button
                                                className="btn-icon text-danger"
                                                title="Delete Namespace"
                                                onClick={() => setDeleteTarget(ns.id)}
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
            )}

            {isCreateOpen && (
                <div className="modal-backdrop">
                    <div className="glass-panel modal">
                        <h3 className="modal-title">Create Namespace</h3>
                        <form onSubmit={handleCreate}>
                            <div className="form-group">
                                <label>Namespace ID</label>
                                <input
                                    type="text"
                                    value={newName}
                                    onChange={e => setNewName(e.target.value)}
                                    placeholder="e.g. fast-api-project"
                                    className="form-input"
                                    autoFocus
                                />
                            </div>
                            {createError && <p className="text-danger small-text mt-2">{createError}</p>}
                            <div className="modal-actions">
                                <button type="button" className="btn btn-secondary" onClick={() => setIsCreateOpen(false)}>Cancel</button>
                                <button type="submit" className="btn btn-primary">Create</button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {deleteTarget && (
                <ConfirmDialog
                    title="Delete Namespace"
                    message={`Are you sure you want to delete the namespace "${deleteTarget}" and all its entities? This action cannot be undone.`}
                    confirmLabel="Delete"
                    variant="danger"
                    onConfirm={() => handleDelete(deleteTarget)}
                    onCancel={() => setDeleteTarget(null)}
                />
            )}

            {errorToast && (
                <div className="error-inline" style={{ position: 'fixed', bottom: '1.5rem', right: '1.5rem', zIndex: 100, maxWidth: '400px' }}>
                    {errorToast}
                </div>
            )}
        </div>
    );
}
