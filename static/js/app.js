/**
 * Gmail AI Cleaner - Frontend Application
 * Handles all UI interactions and API communications
 */

class GmailCleaner {
    constructor() {
        this.csrfToken = document.getElementById('csrfToken').value;
        this.selectedSenders = new Set();
        this.currentSenders = [];
        this.charts = {};
        
        this.init();
    }
    
    init() {
        this.attachEventListeners();
        this.checkAuthStatus();
        this.loadSummaryStats();
        this.startProgressPolling();
    }
    
    // API Helper Methods
    async apiRequest(url, options = {}) {
        const defaults = {
            headers: {
                'Content-Type': 'application/json',
                'X-CSRF-Token': this.csrfToken
            }
        };
        
        try {
            const response = await axios({
                url,
                ...defaults,
                ...options,
                headers: { ...defaults.headers, ...options.headers }
            });
            return response.data;
        } catch (error) {
            this.handleError(error);
            throw error;
        }
    }
    
    handleError(error) {
        const message = error.response?.data?.error || error.message || 'An error occurred';
        this.showToast(message, 'error');
        console.error('API Error:', error);
    }
    
    // Authentication
    async checkAuthStatus() {
        try {
            const result = await this.apiRequest('/api/auth/status');
            if (!result.authenticated) {
                await this.authenticate();
            }
        } catch (error) {
            console.error('Auth check failed:', error);
        }
    }
    
    async authenticate() {
        try {
            this.showToast('Authenticating with Gmail...', 'info');
            const result = await this.apiRequest('/api/auth/login', { method: 'POST' });
            if (result.success) {
                this.showToast('Authentication successful!', 'success');
            }
        } catch (error) {
            this.showToast('Authentication failed. Please refresh and try again.', 'error');
        }
    }
    
    // Load Summary Statistics
    async loadSummaryStats() {
        try {
            const result = await this.apiRequest('/api/stats/summary');
            if (result.success) {
                const data = result.data;
                this.updateElement('totalEmails', this.formatNumber(data.total_emails));
                this.updateElement('uniqueSenders', this.formatNumber(data.total_senders));
                this.updateElement('storageUsed', `${data.total_size_mb} MB`);
                this.updateElement('cleanedWeek', this.formatNumber(data.deleted_last_week));
            }
        } catch (error) {
            console.error('Failed to load stats:', error);
        }
    }
    
    // Sender Analysis
    async analyzeSenders() {
        this.showProgress('Analyzing senders...', 0);
        this.showSection('senderAnalysisSection');
        
        try {
            const result = await this.apiRequest('/api/analyze/senders');
            if (result.success) {
                this.currentSenders = result.data.senders;
                this.renderSenderTable(this.currentSenders);
                this.showToast(`Analyzed ${result.data.total_senders} senders`, 'success');
            }
        } catch (error) {
            this.showToast('Failed to analyze senders', 'error');
        } finally {
            this.hideProgress();
        }
    }
    
    renderSenderTable(senders) {
        const tbody = document.getElementById('senderTableBody');
        tbody.innerHTML = '';
        
        senders.forEach(sender => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td><input type="checkbox" class="sender-checkbox" data-sender="${sender.email}"></td>
                <td class="sender-email">${this.escapeHtml(sender.email)}</td>
                <td>${this.escapeHtml(sender.domain)}</td>
                <td>${sender.total_count}</td>
                <td>${sender.unread_count}</td>
                <td>${sender.size_mb}</td>
                <td><span class="spam-score" data-score="${sender.spam_score}">${(sender.spam_score * 100).toFixed(0)}%</span></td>
                <td>
                    ${sender.is_newsletter ? '<span class="badge badge-info">Newsletter</span>' : ''}
                    ${sender.is_automated ? '<span class="badge badge-warning">Automated</span>' : ''}
                </td>
                <td>
                    <button class="btn btn-sm btn-danger delete-sender" data-sender="${sender.email}">
                        <i class="fas fa-trash"></i>
                    </button>
                    ${sender.has_unsubscribe ? 
                        `<button class="btn btn-sm btn-warning unsubscribe-sender" data-sender="${sender.email}">
                            <i class="fas fa-times-circle"></i>
                        </button>` : ''}
                </td>
            `;
            tbody.appendChild(row);
        });
        
        this.attachSenderTableListeners();
    }
    
    attachSenderTableListeners() {
        // Checkbox selection
        document.querySelectorAll('.sender-checkbox').forEach(checkbox => {
            checkbox.addEventListener('change', (e) => {
                const sender = e.target.dataset.sender;
                if (e.target.checked) {
                    this.selectedSenders.add(sender);
                } else {
                    this.selectedSenders.delete(sender);
                }
                this.updateBulkActionButtons();
            });
        });
        
        // Delete buttons
        document.querySelectorAll('.delete-sender').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const sender = e.target.closest('button').dataset.sender;
                this.confirmDeleteSender(sender);
            });
        });
        
        // Unsubscribe buttons
        document.querySelectorAll('.unsubscribe-sender').forEach(btn => {
            btn.addEventListener('click', (e) => {
                const sender = e.target.closest('button').dataset.sender;
                this.unsubscribeFromSender(sender);
            });
        });
    }
    
    updateBulkActionButtons() {
        const hasSelection = this.selectedSenders.size > 0;
        document.getElementById('deleteSelectedBtn').disabled = !hasSelection;
        document.getElementById('unsubscribeSelectedBtn').disabled = !hasSelection;
    }
    
    // Delete Operations
    async confirmDeleteSender(sender) {
        this.showConfirmModal(
            'Delete All Emails',
            `Are you sure you want to delete all emails from ${sender}?`,
            async () => {
                await this.deleteSender(sender);
            }
        );
    }
    
    async deleteSender(sender, dryRun = false) {
        try {
            this.showProgress(`Deleting emails from ${sender}...`, 0);
            const result = await this.apiRequest('/api/delete/sender', {
                method: 'POST',
                data: { sender, dry_run: dryRun }
            });
            
            if (result.success) {
                this.showToast(result.message, 'success');
                if (!dryRun) {
                    // Refresh the table
                    await this.analyzeSenders();
                }
            }
        } catch (error) {
            this.showToast('Failed to delete emails', 'error');
        } finally {
            this.hideProgress();
        }
    }
    
    async deleteSelected() {
        const count = this.selectedSenders.size;
        this.showConfirmModal(
            'Delete Selected',
            `Delete all emails from ${count} selected sender${count > 1 ? 's' : ''}?`,
            async () => {
                for (const sender of this.selectedSenders) {
                    await this.deleteSender(sender);
                }
                this.selectedSenders.clear();
                this.updateBulkActionButtons();
            }
        );
    }
    
    // Cleanup Suggestions
    async loadSuggestions() {
        try {
            this.showProgress('Loading cleanup suggestions...', 0);
            this.showSection('suggestionsSection');
            
            const result = await this.apiRequest('/api/suggestions');
            if (result.success) {
                this.renderSuggestions(result.data);
            }
        } catch (error) {
            this.showToast('Failed to load suggestions', 'error');
        } finally {
            this.hideProgress();
        }
    }
    
    renderSuggestions(data) {
        const container = document.getElementById('suggestionsList');
        container.innerHTML = '';
        
        // Update impact summary
        this.updateElement('totalImpactEmails', this.formatNumber(data.total_impact.email_count));
        this.updateElement('totalImpactSize', data.total_impact.size_mb.toFixed(1));
        
        data.suggestions.forEach((suggestion, index) => {
            const card = document.createElement('div');
            card.className = 'suggestion-card';
            card.innerHTML = `
                <div class="suggestion-header">
                    <h4>${this.escapeHtml(suggestion.sender)}</h4>
                    <span class="confidence-badge" data-confidence="${suggestion.confidence}">
                        ${(suggestion.confidence * 100).toFixed(0)}% Confidence
                    </span>
                </div>
                <div class="suggestion-reason">${this.escapeHtml(suggestion.reason)}</div>
                <div class="suggestion-impact">
                    <span><i class="fas fa-envelope"></i> ${suggestion.impact.email_count} emails</span>
                    <span><i class="fas fa-hdd"></i> ${suggestion.impact.size_mb.toFixed(1)} MB</span>
                    <span><i class="fas fa-envelope-open"></i> ${suggestion.impact.unread_count} unread</span>
                </div>
                <div class="suggestion-actions">
                    <button class="btn btn-sm btn-danger" onclick="gmailCleaner.deleteSender('${suggestion.sender}')">
                        <i class="fas fa-trash"></i> Delete All
                    </button>
                    <button class="btn btn-sm btn-warning" onclick="gmailCleaner.deleteSender('${suggestion.sender}', true)">
                        <i class="fas fa-eye"></i> Preview
                    </button>
                </div>
            `;
            container.appendChild(card);
        });
    }
    
    // Bulk Cleanup
    async executeBulkCleanup() {
        const criteria = {
            domain: document.getElementById('cleanupDomain').value,
            older_than_days: parseInt(document.getElementById('cleanupDays').value) || null,
            is_unread: document.getElementById('cleanupUnread').checked,
            has_attachment: document.getElementById('cleanupAttachments').checked,
            min_size_mb: parseFloat(document.getElementById('cleanupSize').value) || null,
            exclude_important: document.getElementById('excludeImportant').checked,
            exclude_starred: document.getElementById('excludeStarred').checked,
            dry_run: document.getElementById('dryRun').checked
        };
        
        // Remove null values
        Object.keys(criteria).forEach(key => {
            if (criteria[key] === null || criteria[key] === '') {
                delete criteria[key];
            }
        });
        
        try {
            this.showProgress('Processing bulk cleanup...', 0);
            const result = await this.apiRequest('/api/delete/bulk', {
                method: 'POST',
                data: criteria
            });
            
            if (result.success) {
                this.showToast(result.message, 'success');
                if (result.emails) {
                    // Show preview
                    this.showBulkPreview(result);
                }
            }
        } catch (error) {
            this.showToast('Bulk cleanup failed', 'error');
        } finally {
            this.hideProgress();
            this.closeModal('bulkCleanupModal');
        }
    }
    
    showBulkPreview(result) {
        let preview = `<h3>Preview: ${result.count} emails will be deleted</h3>`;
        if (result.emails && result.emails.length > 0) {
            preview += '<ul class="email-preview">';
            result.emails.forEach(email => {
                preview += `<li>${this.escapeHtml(email.sender)} - ${this.escapeHtml(email.subject)}</li>`;
            });
            preview += '</ul>';
        }
        
        this.showConfirmModal('Bulk Delete Preview', preview, async () => {
            // Execute without dry_run
            document.getElementById('dryRun').checked = false;
            await this.executeBulkCleanup();
        });
    }
    
    // Unsubscribe
    async unsubscribeFromSender(sender) {
        try {
            const result = await this.apiRequest('/api/unsubscribe', {
                method: 'POST',
                data: { sender }
            });
            
            if (result.success && result.links) {
                let message = `<p>Found unsubscribe options for ${sender}:</p><ul>`;
                result.links.forEach(link => {
                    message += `<li><a href="${link}" target="_blank" rel="noopener">${link}</a></li>`;
                });
                message += '</ul><p>Click the links above to unsubscribe.</p>';
                
                this.showModal('Unsubscribe Options', message);
            } else {
                this.showToast(result.message || 'No unsubscribe options found', 'warning');
            }
        } catch (error) {
            this.showToast('Failed to find unsubscribe options', 'error');
        }
    }
    
    // Charts
    async loadVelocityChart() {
        try {
            this.showSection('velocitySection');
            const result = await this.apiRequest('/api/stats/velocity?days=30');
            
            if (result.success) {
                this.renderVelocityChart(result.data);
            }
        } catch (error) {
            this.showToast('Failed to load velocity data', 'error');
        }
    }
    
    renderVelocityChart(data) {
        const ctx = document.getElementById('velocityChart').getContext('2d');
        
        if (this.charts.velocity) {
            this.charts.velocity.destroy();
        }
        
        this.charts.velocity = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.daily_totals.map(d => d[0]),
                datasets: [{
                    label: 'Total Emails',
                    data: data.daily_totals.map(d => d[1]),
                    borderColor: 'rgb(75, 192, 192)',
                    tension: 0.1
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    title: {
                        display: true,
                        text: 'Email Volume Over Time'
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true
                    }
                }
            }
        });
    }
    
    async loadDomainChart() {
        try {
            this.showSection('domainSection');
            const result = await this.apiRequest('/api/analyze/domains');
            
            if (result.success) {
                this.renderDomainChart(result.data);
            }
        } catch (error) {
            this.showToast('Failed to load domain data', 'error');
        }
    }
    
    renderDomainChart(data) {
        const ctx = document.getElementById('domainChart').getContext('2d');
        
        if (this.charts.domain) {
            this.charts.domain.destroy();
        }
        
        const top10 = data.domains.slice(0, 10);
        
        this.charts.domain = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: top10.map(d => d.domain),
                datasets: [{
                    data: top10.map(d => d.count),
                    backgroundColor: [
                        '#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF',
                        '#FF9F40', '#FF6384', '#C0C0C0', '#4BC0C0', '#36A2EB'
                    ]
                }]
            },
            options: {
                responsive: true,
                plugins: {
                    title: {
                        display: true,
                        text: 'Top 10 Email Domains'
                    },
                    legend: {
                        position: 'right'
                    }
                }
            }
        });
    }
    
    // Export Statistics
    async exportStats() {
        try {
            window.location.href = '/api/export/csv';
            this.showToast('Exporting statistics...', 'info');
        } catch (error) {
            this.showToast('Export failed', 'error');
        }
    }
    
    // Progress Polling
    startProgressPolling() {
        setInterval(async () => {
            if (this.isProgressVisible()) {
                try {
                    const result = await this.apiRequest('/api/progress');
                    if (result.current_operation) {
                        this.updateProgress(result.percentage, result.message);
                    }
                } catch (error) {
                    console.error('Progress polling error:', error);
                }
            }
        }, 1000);
    }
    
    // UI Helper Methods
    showSection(sectionId) {
        document.getElementById(sectionId).style.display = 'block';
    }
    
    hideSection(sectionId) {
        document.getElementById(sectionId).style.display = 'none';
    }
    
    showProgress(message, percentage) {
        const container = document.getElementById('progressContainer');
        container.style.display = 'block';
        this.updateProgress(percentage, message);
    }
    
    updateProgress(percentage, message) {
        document.getElementById('progressMessage').textContent = message;
        document.getElementById('progressBar').style.width = `${percentage}%`;
        document.getElementById('progressDetails').textContent = `${Math.round(percentage)}% complete`;
    }
    
    hideProgress() {
        document.getElementById('progressContainer').style.display = 'none';
    }
    
    isProgressVisible() {
        return document.getElementById('progressContainer').style.display !== 'none';
    }
    
    showModal(title, content, onConfirm) {
        const modal = document.getElementById('confirmModal');
        modal.querySelector('.modal-header h2').textContent = title;
        modal.querySelector('#confirmMessage').innerHTML = content;
        modal.style.display = 'block';
        
        if (onConfirm) {
            const confirmBtn = document.getElementById('confirmActionBtn');
            confirmBtn.onclick = () => {
                onConfirm();
                this.closeModal('confirmModal');
            };
        }
    }
    
    showConfirmModal(title, message, onConfirm) {
        this.showModal(title, message, onConfirm);
    }
    
    closeModal(modalId) {
        document.getElementById(modalId).style.display = 'none';
    }
    
    showToast(message, type = 'info') {
        const container = document.getElementById('toastContainer');
        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.innerHTML = `
            <i class="fas ${this.getToastIcon(type)}"></i>
            <span>${this.escapeHtml(message)}</span>
        `;
        
        container.appendChild(toast);
        
        setTimeout(() => {
            toast.classList.add('show');
        }, 10);
        
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => {
                container.removeChild(toast);
            }, 300);
        }, 3000);
    }
    
    getToastIcon(type) {
        const icons = {
            success: 'fa-check-circle',
            error: 'fa-exclamation-circle',
            warning: 'fa-exclamation-triangle',
            info: 'fa-info-circle'
        };
        return icons[type] || icons.info;
    }
    
    updateElement(id, value) {
        const element = document.getElementById(id);
        if (element) {
            element.textContent = value;
        }
    }
    
    formatNumber(num) {
        return new Intl.NumberFormat().format(num);
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    // Event Listeners
    attachEventListeners() {
        // Quick Actions
        document.getElementById('analyzeSendersBtn').addEventListener('click', () => this.analyzeSenders());
        document.getElementById('viewSuggestionsBtn').addEventListener('click', () => this.loadSuggestions());
        document.getElementById('bulkCleanupBtn').addEventListener('click', () => {
            document.getElementById('bulkCleanupModal').style.display = 'block';
        });
        document.getElementById('exportStatsBtn').addEventListener('click', () => this.exportStats());
        
        // Header Actions
        document.getElementById('refreshBtn').addEventListener('click', () => {
            this.loadSummaryStats();
            this.showToast('Refreshing data...', 'info');
        });
        
        document.getElementById('settingsBtn').addEventListener('click', () => {
            document.getElementById('settingsModal').style.display = 'block';
        });
        
        // Sender Table Controls
        document.getElementById('senderSearch').addEventListener('input', (e) => {
            this.filterSenders(e.target.value);
        });
        
        document.getElementById('sortBy').addEventListener('change', (e) => {
            this.sortSenders(e.target.value);
        });
        
        document.getElementById('selectAllBtn').addEventListener('click', () => {
            document.querySelectorAll('.sender-checkbox').forEach(cb => {
                cb.checked = true;
                this.selectedSenders.add(cb.dataset.sender);
            });
            this.updateBulkActionButtons();
        });
        
        document.getElementById('deselectAllBtn').addEventListener('click', () => {
            document.querySelectorAll('.sender-checkbox').forEach(cb => {
                cb.checked = false;
            });
            this.selectedSenders.clear();
            this.updateBulkActionButtons();
        });
        
        document.getElementById('deleteSelectedBtn').addEventListener('click', () => {
            this.deleteSelected();
        });
        
        // Modal Controls
        document.querySelectorAll('.close-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.target.closest('.modal').style.display = 'none';
            });
        });
        
        // Bulk Cleanup Form
        document.getElementById('executeBulkCleanup').addEventListener('click', () => {
            this.executeBulkCleanup();
        });
        
        // Click outside modal to close
        window.addEventListener('click', (e) => {
            if (e.target.classList.contains('modal')) {
                e.target.style.display = 'none';
            }
        });
    }
    
    filterSenders(searchTerm) {
        const filtered = this.currentSenders.filter(sender => 
            sender.email.toLowerCase().includes(searchTerm.toLowerCase()) ||
            sender.domain.toLowerCase().includes(searchTerm.toLowerCase())
        );
        this.renderSenderTable(filtered);
    }
    
    sortSenders(sortBy) {
        const sorted = [...this.currentSenders].sort((a, b) => {
            switch (sortBy) {
                case 'total_count':
                    return b.total_count - a.total_count;
                case 'unread_count':
                    return b.unread_count - a.unread_count;
                case 'size_mb':
                    return b.size_mb - a.size_mb;
                case 'spam_score':
                    return b.spam_score - a.spam_score;
                case 'email_velocity':
                    return b.email_velocity - a.email_velocity;
                default:
                    return 0;
            }
        });
        this.renderSenderTable(sorted);
    }
}

// Initialize app when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.gmailCleaner = new GmailCleaner();
});
