/**
 * Waitlist form component.
 * 
 * Collects user information for waitlist signup:
 * - Work email, name, title, company name, num employees
 * - Multi-select for apps of interest
 * - Multi-select for core needs
 */

import { useState } from 'react';
import type { IconType } from 'react-icons';
import {
  SiSalesforce,
  SiHubspot,
  SiSlack,
  SiZoom,
  SiGooglecalendar,
  SiGmail,
} from 'react-icons/si';
import { HiMusicNote, HiOutlineCalendar, HiOutlineMail, HiMicrophone } from 'react-icons/hi';
import { API_BASE } from '../lib/api';

interface WaitlistFormProps {
  onClose: () => void;
  onSuccess: () => void;
}

interface FormData {
  email: string;
  name: string;
  title: string;
  company_name: string;
  num_employees: string;
  apps_of_interest: string[];
  core_needs: string[];
}

const EMPLOYEE_OPTIONS: { value: string; label: string }[] = [
  { value: '1-10', label: '1-10' },
  { value: '11-50', label: '11-50' },
  { value: '51-200', label: '51-200' },
  { value: '201-500', label: '201-500' },
  { value: '501-1000', label: '501-1,000' },
  { value: '1000+', label: '1,000+' },
];

const APP_OPTIONS: { value: string; label: string; color: string; icon: IconType }[] = [
  { value: 'salesforce', label: 'Salesforce', color: 'bg-blue-500', icon: SiSalesforce },
  { value: 'hubspot', label: 'HubSpot', color: 'bg-orange-500', icon: SiHubspot },
  { value: 'slack', label: 'Slack', color: 'bg-purple-500', icon: SiSlack },
  { value: 'zoom', label: 'Zoom', color: 'bg-blue-400', icon: SiZoom },
  { value: 'google_calendar', label: 'Google Calendar', color: 'bg-green-500', icon: SiGooglecalendar },
  { value: 'gmail', label: 'Gmail', color: 'bg-red-500', icon: SiGmail },
  { value: 'microsoft_calendar', label: 'Outlook Calendar', color: 'bg-sky-500', icon: HiOutlineCalendar },
  { value: 'microsoft_mail', label: 'Outlook Mail', color: 'bg-sky-600', icon: HiOutlineMail },
  { value: 'gong', label: 'Gong', color: 'bg-violet-500', icon: HiMicrophone },
  { value: 'chorus', label: 'Chorus', color: 'bg-indigo-500', icon: HiMusicNote },
];

const NEEDS_OPTIONS: { value: string; label: string }[] = [
  { value: 'query_crm', label: 'Easily query CRM data' },
  { value: 'combine_insights', label: 'New insights by combining CRM, meeting notes, email, calendar' },
  { value: 'automations', label: 'Set up AI automations' },
  { value: 'clean_data', label: 'Clean up data in CRM' },
  { value: 'track_performance', label: 'Track team performance' },
  { value: 'dropped_deals', label: 'Identify dropped deals' },
  { value: 'forecasting', label: 'Improve forecasting accuracy' },
  { value: 'coaching', label: 'Coach reps with data' },
];

export function WaitlistForm({ onClose, onSuccess }: WaitlistFormProps): JSX.Element {
  const [formData, setFormData] = useState<FormData>({
    email: '',
    name: '',
    title: '',
    company_name: '',
    num_employees: '',
    apps_of_interest: [],
    core_needs: [],
  });
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>): void => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  const toggleMultiSelect = (field: 'apps_of_interest' | 'core_needs', value: string): void => {
    setFormData((prev) => {
      const current = prev[field];
      const updated = current.includes(value)
        ? current.filter((v) => v !== value)
        : [...current, value];
      return { ...prev, [field]: updated };
    });
  };

  const handleSubmit = async (e: React.FormEvent): Promise<void> => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);

    try {
      const response = await fetch(`${API_BASE}/waitlist`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formData),
      });

      const data = await response.json() as { success: boolean; message: string };

      if (data.success) {
        onSuccess();
      } else {
        setError(data.message || 'Something went wrong. Please try again.');
      }
    } catch (err) {
      setError('Failed to submit. Please check your connection and try again.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const isValid =
    formData.email &&
    formData.name &&
    formData.title &&
    formData.company_name &&
    formData.num_employees &&
    formData.apps_of_interest.length > 0 &&
    formData.core_needs.length > 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div className="relative w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-2xl bg-surface-900 border border-surface-700 shadow-2xl">
        {/* Header */}
        <div className="sticky top-0 z-10 flex items-center justify-between p-6 pb-4 bg-surface-900 border-b border-surface-800">
          <div>
            <h2 className="text-xl font-bold text-surface-50">Join the Waitlist</h2>
            <p className="text-sm text-surface-400 mt-1">Get early access to Revtops</p>
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg text-surface-400 hover:text-surface-200 hover:bg-surface-800 transition-colors"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Form */}
        <form onSubmit={(e) => void handleSubmit(e)} className="p-6 space-y-5">
          {/* Email */}
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-surface-300 mb-1.5">
              Work Email *
            </label>
            <input
              type="email"
              id="email"
              name="email"
              value={formData.email}
              onChange={handleInputChange}
              placeholder="you@company.com"
              required
              className="w-full px-4 py-2.5 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
          </div>

          {/* Name */}
          <div>
            <label htmlFor="name" className="block text-sm font-medium text-surface-300 mb-1.5">
              Full Name *
            </label>
            <input
              type="text"
              id="name"
              name="name"
              value={formData.name}
              onChange={handleInputChange}
              placeholder="Jane Smith"
              required
              className="w-full px-4 py-2.5 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
          </div>

          {/* Title */}
          <div>
            <label htmlFor="title" className="block text-sm font-medium text-surface-300 mb-1.5">
              Job Title *
            </label>
            <input
              type="text"
              id="title"
              name="title"
              value={formData.title}
              onChange={handleInputChange}
              placeholder="VP of Sales"
              required
              className="w-full px-4 py-2.5 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
          </div>

          {/* Company Name */}
          <div>
            <label htmlFor="company_name" className="block text-sm font-medium text-surface-300 mb-1.5">
              Company Name *
            </label>
            <input
              type="text"
              id="company_name"
              name="company_name"
              value={formData.company_name}
              onChange={handleInputChange}
              placeholder="Acme Corp"
              required
              className="w-full px-4 py-2.5 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 placeholder-surface-500 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            />
          </div>

          {/* Number of Employees */}
          <div>
            <label htmlFor="num_employees" className="block text-sm font-medium text-surface-300 mb-1.5">
              Number of Employees *
            </label>
            <select
              id="num_employees"
              name="num_employees"
              value={formData.num_employees}
              onChange={handleInputChange}
              required
              className="w-full px-4 py-2.5 rounded-lg bg-surface-800 border border-surface-700 text-surface-100 focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
            >
              <option value="">Select...</option>
              {EMPLOYEE_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </div>

          {/* Apps of Interest */}
          <div>
            <label className="block text-sm font-medium text-surface-300 mb-2">
              Which apps do you most want to connect? *
            </label>
            <div className="grid grid-cols-2 gap-2">
              {APP_OPTIONS.map((app) => (
                <button
                  key={app.value}
                  type="button"
                  onClick={() => toggleMultiSelect('apps_of_interest', app.value)}
                  className={`flex items-center gap-2.5 px-3 py-2 rounded-lg border text-sm text-left transition-colors ${
                    formData.apps_of_interest.includes(app.value)
                      ? 'bg-primary-500/20 border-primary-500 text-primary-300'
                      : 'bg-surface-800 border-surface-700 text-surface-300 hover:border-surface-600'
                  }`}
                >
                  <div className={`${app.color} p-1.5 rounded-md text-white flex-shrink-0`}>
                    <app.icon className="w-4 h-4" />
                  </div>
                  <span>{app.label}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Core Needs */}
          <div>
            <label className="block text-sm font-medium text-surface-300 mb-2">
              What are your core needs? *
            </label>
            <div className="space-y-2">
              {NEEDS_OPTIONS.map((need) => (
                <button
                  key={need.value}
                  type="button"
                  onClick={() => toggleMultiSelect('core_needs', need.value)}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg border text-sm text-left transition-colors ${
                    formData.core_needs.includes(need.value)
                      ? 'bg-primary-500/20 border-primary-500 text-primary-300'
                      : 'bg-surface-800 border-surface-700 text-surface-300 hover:border-surface-600'
                  }`}
                >
                  <div
                    className={`w-4 h-4 rounded border flex items-center justify-center flex-shrink-0 ${
                      formData.core_needs.includes(need.value)
                        ? 'bg-primary-500 border-primary-500'
                        : 'border-surface-600'
                    }`}
                  >
                    {formData.core_needs.includes(need.value) && (
                      <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                  </div>
                  <span>{need.label}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Error */}
          {error && (
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
              {error}
            </div>
          )}

          {/* Submit */}
          <button
            type="submit"
            disabled={!isValid || isSubmitting}
            className="w-full btn-primary py-3 text-base disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isSubmitting ? 'Submitting...' : 'Join the Waitlist'}
          </button>
        </form>
      </div>
    </div>
  );
}
