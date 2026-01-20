/**
 * Onboarding component.
 * 
 * Guides new users through connecting their data sources.
 * Can be skipped and resumed later from the main interface.
 */

import { useState } from 'react';

interface Integration {
  id: string;
  name: string;
  description: string;
  icon: JSX.Element;
  color: string;
  connected: boolean;
}

interface OnboardingProps {
  onComplete: () => void;
  onSkip: () => void;
}

export function Onboarding({ onComplete, onSkip }: OnboardingProps): JSX.Element {
  const [integrations, setIntegrations] = useState<Integration[]>([
    {
      id: 'hubspot',
      name: 'HubSpot',
      description: 'Sync deals, contacts, and companies from your CRM',
      icon: (
        <svg className="w-6 h-6" viewBox="0 0 24 24" fill="currentColor">
          <path d="M17.5 14.5c0 .82-.67 1.5-1.5 1.5s-1.5-.68-1.5-1.5.67-1.5 1.5-1.5 1.5.68 1.5 1.5zm-9-1.5c-.83 0-1.5.68-1.5 1.5s.67 1.5 1.5 1.5 1.5-.68 1.5-1.5-.67-1.5-1.5-1.5zm12.5 1.5c0 2.49-2.01 4.5-4.5 4.5-.88 0-1.7-.26-2.4-.69l-1.6.69c-.1.04-.21.06-.32.06-.26 0-.51-.1-.71-.29-.27-.27-.35-.68-.21-1.03l.69-1.6c-.43-.7-.69-1.52-.69-2.4V14c0-.35.04-.69.11-1.01.18-.02.36-.03.54-.03 2.43 0 4.5 1.56 5.27 3.73.44-.15.79-.47.97-.88.02-.05.04-.1.05-.15.53-1.34.05-2.88-1.16-3.72-.61-.42-1.33-.64-2.08-.64H12V9c0-.55-.45-1-1-1H7c-.55 0-1 .45-1 1v3c-1.66 0-3 1.34-3 3s1.34 3 3 3h.18c.62 1.76 2.28 3 4.22 3h5.1c2.49 0 4.5-2.01 4.5-4.5z"/>
        </svg>
      ),
      color: 'from-orange-500 to-red-500',
      connected: false,
    },
    {
      id: 'slack',
      name: 'Slack',
      description: 'Search conversations and track deal discussions',
      icon: (
        <svg className="w-6 h-6" viewBox="0 0 24 24" fill="currentColor">
          <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zm1.271 0a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313zM8.834 5.042a2.528 2.528 0 0 1-2.521-2.52A2.528 2.528 0 0 1 8.834 0a2.528 2.528 0 0 1 2.521 2.522v2.52H8.834zm0 1.271a2.528 2.528 0 0 1 2.521 2.521 2.528 2.528 0 0 1-2.521 2.521H2.522A2.528 2.528 0 0 1 0 8.834a2.528 2.528 0 0 1 2.522-2.521h6.312zm10.124 2.521a2.528 2.528 0 0 1 2.522-2.521A2.528 2.528 0 0 1 24 8.834a2.528 2.528 0 0 1-2.52 2.521h-2.522V8.834zm-1.271 0a2.528 2.528 0 0 1-2.521 2.521 2.528 2.528 0 0 1-2.521-2.521V2.522A2.528 2.528 0 0 1 15.166 0a2.528 2.528 0 0 1 2.521 2.522v6.312zm-2.521 10.124a2.528 2.528 0 0 1 2.521 2.522A2.528 2.528 0 0 1 15.166 24a2.528 2.528 0 0 1-2.521-2.52v-2.522h2.521zm0-1.271a2.528 2.528 0 0 1-2.521-2.521 2.528 2.528 0 0 1 2.521-2.521h6.312A2.528 2.528 0 0 1 24 15.166a2.528 2.528 0 0 1-2.52 2.521h-6.313z"/>
        </svg>
      ),
      color: 'from-purple-500 to-pink-500',
      connected: false,
    },
    {
      id: 'google_calendar',
      name: 'Google Calendar',
      description: 'See meetings, calls, and activity timelines',
      icon: (
        <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
        </svg>
      ),
      color: 'from-blue-500 to-cyan-500',
      connected: false,
    },
  ]);

  const [connecting, setConnecting] = useState<string | null>(null);

  const handleConnect = async (integrationId: string) => {
    setConnecting(integrationId);
    
    // In production, this would redirect to the OAuth flow
    // For now, we'll simulate the connection
    // window.location.href = `/api/auth/connect/${integrationId}`;
    
    // Simulate connection for demo
    await new Promise(resolve => setTimeout(resolve, 1500));
    
    setIntegrations(prev => 
      prev.map(int => 
        int.id === integrationId ? { ...int, connected: true } : int
      )
    );
    setConnecting(null);
  };

  const connectedCount = integrations.filter(i => i.connected).length;

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      {/* Background effects */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-1/2 -right-1/4 w-[800px] h-[800px] rounded-full bg-gradient-to-br from-primary-600/20 to-transparent blur-3xl" />
        <div className="absolute -bottom-1/4 -left-1/4 w-[600px] h-[600px] rounded-full bg-gradient-to-tr from-emerald-600/10 to-transparent blur-3xl" />
      </div>

      <div className="relative z-10 w-full max-w-2xl">
        {/* Progress indicator */}
        <div className="flex items-center justify-center gap-2 mb-8">
          <div className="flex items-center gap-1">
            {[0, 1, 2].map((step) => (
              <div
                key={step}
                className={`w-2 h-2 rounded-full transition-colors ${
                  step < connectedCount ? 'bg-primary-500' : 'bg-surface-700'
                }`}
              />
            ))}
          </div>
          <span className="text-sm text-surface-500 ml-2">
            {connectedCount} of {integrations.length} connected
          </span>
        </div>

        {/* Header */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-primary-500 to-primary-700 mb-6">
            <svg className="w-8 h-8 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1" />
            </svg>
          </div>
          <h1 className="text-3xl font-bold text-surface-50 mb-3">
            Connect your data sources
          </h1>
          <p className="text-surface-400 text-lg max-w-md mx-auto">
            Link your tools to start getting AI-powered insights. You can always add more later.
          </p>
        </div>

        {/* Integration cards */}
        <div className="space-y-4 mb-8">
          {integrations.map((integration) => (
            <div
              key={integration.id}
              className={`card flex items-center gap-4 transition-all ${
                integration.connected ? 'ring-2 ring-emerald-500/50' : ''
              }`}
            >
              <div className={`w-12 h-12 rounded-xl bg-gradient-to-br ${integration.color} flex items-center justify-center text-white flex-shrink-0`}>
                {integration.icon}
              </div>
              
              <div className="flex-1 min-w-0">
                <h3 className="font-semibold text-surface-100">{integration.name}</h3>
                <p className="text-sm text-surface-400 truncate">{integration.description}</p>
              </div>

              {integration.connected ? (
                <div className="flex items-center gap-2 text-emerald-400">
                  <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  <span className="text-sm font-medium">Connected</span>
                </div>
              ) : (
                <button
                  onClick={() => handleConnect(integration.id)}
                  disabled={connecting !== null}
                  className="px-4 py-2 rounded-lg bg-surface-700 hover:bg-surface-600 text-surface-200 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {connecting === integration.id ? (
                    <span className="inline-flex items-center gap-2">
                      <div className="w-4 h-4 border-2 border-surface-400/30 border-t-surface-400 rounded-full animate-spin" />
                      Connecting...
                    </span>
                  ) : (
                    'Connect'
                  )}
                </button>
              )}
            </div>
          ))}
        </div>

        {/* Actions */}
        <div className="flex flex-col sm:flex-row gap-3 justify-center">
          {connectedCount > 0 ? (
            <button
              onClick={onComplete}
              className="btn-primary px-8 py-3 inline-flex items-center justify-center gap-2"
            >
              Continue to Dashboard
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
              </svg>
            </button>
          ) : (
            <button
              onClick={onSkip}
              className="px-8 py-3 rounded-lg text-surface-400 hover:text-surface-200 transition-colors"
            >
              Skip for now
            </button>
          )}
        </div>

        {connectedCount > 0 && (
          <p className="text-center text-surface-500 text-sm mt-4">
            You can connect more sources anytime from settings
          </p>
        )}
      </div>
    </div>
  );
}
