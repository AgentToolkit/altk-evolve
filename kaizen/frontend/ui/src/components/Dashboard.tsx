import { useEffect, useState } from 'react';
import { Activity, Database, FileText, XCircle } from 'lucide-react';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from 'recharts';

interface TypeBreakdown {
    type: string;
    count: number;
}

interface Entity {
    id: string;
    type: string;
    content: string;
    namespace: string;
    created_at: string;
}

interface DashboardData {
    health: boolean;
    namespace_count: number;
    total_entities: number;
    type_breakdown: TypeBreakdown[];
    recent_entities: Entity[];
}

const COLORS = ['#4F46E5', '#8B5CF6', '#EC4899', '#10B981', '#F59E0B', '#3B82F6', '#6366F1'];

export default function Dashboard() {
    const [data, setData] = useState<DashboardData | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        fetch('/api/dashboard')
            .then((res) => {
                if (!res.ok) throw new Error('Failed to fetch dashboard data');
                return res.json();
            })
            .then((d) => {
                setData(d);
                setLoading(false);
            })
            .catch((err) => {
                setError(err.message || 'Error occurred');
                setLoading(false);
            });
    }, []);

    if (loading) {
        return (
            <div className="loading-state">
                <div className="loader"></div>
                <p>Initializing Kaizen UI Dashboard...</p>
            </div>
        );
    }

    if (error || !data) {
        return (
            <div className="error-state">
                <XCircle size={48} />
                <h2>Connection Error</h2>
                <p>{error}</p>
                <button className="retry-btn" onClick={() => window.location.reload()}>Retry</button>
            </div>
        );
    }

    return (
        <div className="dashboard-container">
            <div className="dashboard-grid">
                <div className="glass-panel metric-card">
                    <div className="metric-card-header">
                        <Database size={20} className="text-primary" />
                        <span>Total Namespaces</span>
                    </div>
                    <div className="metric-card-value">{data.namespace_count}</div>
                </div>

                <div className="glass-panel metric-card">
                    <div className="metric-card-header">
                        <FileText size={20} className="text-primary" />
                        <span>Total Entities</span>
                    </div>
                    <div className="metric-card-value">{data.total_entities}</div>
                </div>

                <div className="glass-panel metric-card">
                    <div className="metric-card-header">
                        <Activity size={20} className="text-primary" />
                        <span>Backend Health</span>
                    </div>
                    <div className="health-status">
                        {data.health ? (
                            <>
                                <div className="status-dot"></div>
                                <span>Healthy</span>
                            </>
                        ) : (
                            <>
                                <div className="status-dot error"></div>
                                <span className="text-danger">Unhealthy</span>
                            </>
                        )}
                    </div>
                </div>
            </div>

            <div className="charts-row">
                <div className="glass-panel section-card">
                    <h3 className="section-title">Entity Types</h3>
                    <div className="chart-container">
                        {data.type_breakdown.length > 0 ? (
                            <ResponsiveContainer width="100%" height="100%">
                                <PieChart>
                                    <Pie
                                        data={data.type_breakdown}
                                        innerRadius={60}
                                        outerRadius={80}
                                        paddingAngle={5}
                                        dataKey="count"
                                        nameKey="type"
                                        stroke="none"
                                    >
                                        {data.type_breakdown.map((_entry, index) => (
                                            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                                        ))}
                                    </Pie>
                                    <Tooltip
                                        contentStyle={{ backgroundColor: '#1E293B', border: 'none', borderRadius: '8px', color: '#F8FAFC' }}
                                    />
                                </PieChart>
                            </ResponsiveContainer>
                        ) : (
                            <p className="loading-state" style={{ minHeight: 'auto' }}>No entities found</p>
                        )}
                    </div>
                </div>

                <div className="glass-panel section-card">
                    <h3 className="section-title">Recent Activity</h3>
                    <div className="entity-list">
                        {data.recent_entities.length > 0 ? (
                            data.recent_entities.map((ent) => (
                                <div key={ent.id} className="entity-item">
                                    <div className="entity-icon">
                                        <FileText size={20} />
                                    </div>
                                    <div className="entity-content">
                                        <div className="entity-header">
                                            <span className="entity-type">{ent.type}</span>
                                            <span className="entity-namespace">{ent.namespace}</span>
                                        </div>
                                        <div className="entity-text">{ent.content}</div>
                                    </div>
                                </div>
                            ))
                        ) : (
                            <p className="loading-state" style={{ minHeight: 'auto' }}>No recent entities</p>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
}
