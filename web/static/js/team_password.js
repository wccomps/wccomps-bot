/**
 * Reset Password for one team (admin/includes/team_password_reset.html). The component's root carries
 * data-url, the team's admin_team_action URL.
 */
document.addEventListener('alpine:init', () => {
    Alpine.data('teamPasswordReset', () => compose(toastMixin(), {
        loading: false,
        newPassword: '',

        async resetPassword() {
            if (!confirm('Reset this team\'s password?\n\nThis will:\n- Generate a new password and set it in Authentik\n- End the team account\'s Authentik sessions\n\nDiscord links are kept. Anyone using the old password must use the new one.')) {
                return;
            }

            this.loading = true;
            this.message = '';
            this.newPassword = '';

            try {
                const result = await wcPost(this.$root.dataset.url, { action: 'reset_password' });
                this.message = result.message || result.error || 'Password reset failed';
                this.messageType = result.success ? 'success' : 'error';
                // No reload: the new password stays on screen until you leave the page
                if (result.password) this.newPassword = result.password;
            } catch (e) {
                this.message = 'Request failed: ' + e.message;
                this.messageType = 'error';
            }

            this.loading = false;
        },
    }));
});
