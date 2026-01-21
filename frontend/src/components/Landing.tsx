/**
 * Landing page component.
 * 
 * Public-facing marketing page with hero, features, and CTA.
 */

import { useEffect, useState } from 'react';

interface LandingProps {
  onGetStarted: () => void;
}

export function Landing({ onGetStarted }: LandingProps): JSX.Element {
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    setIsVisible(true);
  }, []);

  return (
    <div className="min-h-screen bg-surface-950 overflow-hidden">
      {/* Gradient background effect */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-1/2 -right-1/4 w-[800px] h-[800px] rounded-full bg-gradient-to-br from-primary-600/20 to-transparent blur-3xl" />
        <div className="absolute -bottom-1/4 -left-1/4 w-[600px] h-[600px] rounded-full bg-gradient-to-tr from-emerald-600/10 to-transparent blur-3xl" />
      </div>

      {/* Navigation */}
      <nav className="relative z-10 flex items-center justify-between px-6 py-4 max-w-7xl mx-auto">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
            <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
            </svg>
          </div>
          <span className="text-xl font-bold text-surface-50">Revtops</span>
        </div>
        <button
          onClick={onGetStarted}
          className="px-4 py-2 text-sm font-medium text-surface-300 hover:text-white transition-colors"
        >
          Sign In
        </button>
      </nav>

      {/* Hero Section */}
      <section className="relative z-10 max-w-7xl mx-auto px-6 pt-20 pb-32">
        <div
          className={`text-center transition-all duration-1000 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-8'
            }`}
        >
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary-500/10 border border-primary-500/20 text-primary-400 text-sm mb-6">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-primary-500"></span>
            </span>
            AI-Powered Revenue Intelligence
          </div>

          <h1 className="text-5xl md:text-7xl font-bold text-surface-50 mb-6 leading-tight">
            Your Revenue Copilot
            <br />
          </h1>

          <p className="text-xl text-surface-400 max-w-2xl mx-auto mb-10">
            Connect your CRM, Slack, email, calendar, meeting notes, and more.
            <br />
            Ask questions in plain English and get instant insights about your pipeline, deals, and team performance.
          </p>

          <div className="flex flex-col sm:flex-row gap-4 justify-center">
            <button
              onClick={onGetStarted}
              className="btn-primary text-lg px-8 py-4 inline-flex items-center gap-2"
            >
              Get Started Free
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
              </svg>
            </button>
            <button className="px-8 py-4 rounded-lg font-medium text-surface-300 border border-surface-700 hover:border-surface-500 transition-colors">
              Watch Demo
            </button>
          </div>
        </div>

        {/* Hero Visual */}
        <div
          className={`mt-20 transition-all duration-1000 delay-300 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-8'
            }`}
        >
          <div className="relative max-w-4xl mx-auto">
            <div className="absolute inset-0 bg-gradient-to-t from-surface-950 via-transparent to-transparent z-10 pointer-events-none" />
            <div className="rounded-xl border border-surface-800 bg-surface-900/50 backdrop-blur-sm overflow-hidden shadow-2xl">
              {/* Mock chat interface */}
              <div className="flex items-center gap-2 px-4 py-3 border-b border-surface-800 bg-surface-900/80">
                <div className="w-3 h-3 rounded-full bg-red-500" />
                <div className="w-3 h-3 rounded-full bg-yellow-500" />
                <div className="w-3 h-3 rounded-full bg-green-500" />
              </div>
              <div className="p-6 space-y-4">
                <div className="flex gap-3">
                  <div className="w-8 h-8 rounded-full bg-surface-700 flex-shrink-0" />
                  <div className="bg-surface-800 rounded-2xl rounded-tl-sm px-4 py-3 max-w-md">
                    <p className="text-surface-200">Show me all deals closing this month over $50k</p>
                  </div>
                </div>
                <div className="flex gap-3 justify-end">
                  <div className="bg-primary-600/20 border border-primary-500/30 rounded-2xl rounded-tr-sm px-4 py-3 max-w-lg">
                    <p className="text-surface-200 mb-2">Found 8 deals closing in January over $50k:</p>
                    <div className="text-sm text-surface-400 space-y-1">
                      <p>• Acme Corp - $125,000 (Negotiation)</p>
                      <p>• TechStart Inc - $89,000 (Proposal)</p>
                      <p>• Global Systems - $67,500 (Contract)</p>
                    </div>
                  </div>
                  <div className="w-8 h-8 rounded-full bg-gradient-to-br from-primary-500 to-primary-700 flex-shrink-0 flex items-center justify-center">
                    <svg className="w-4 h-4 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                    </svg>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="relative z-10 max-w-7xl mx-auto px-6 py-24">
        <h2 className="text-3xl font-bold text-surface-50 text-center mb-4">
          Everything you need, connected
        </h2>
        <p className="text-surface-400 text-center max-w-xl mx-auto mb-16">
          Pull data from all your revenue tools into one intelligent interface.
        </p>

        <div className="grid md:grid-cols-3 gap-8">
          {[
            {
              icon: (
                <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                </svg>
              ),
              title: 'CRM Integration',
              description: 'Connect HubSpot or Salesforce. Query your deals, contacts, and pipeline in natural language.',
              color: 'from-orange-500 to-red-500',
            },
            {
              icon: (
                <svg className="w-6 h-6" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zm1.271 0a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z" />
                </svg>
              ),
              title: 'Slack Integration',
              description: 'Search conversations, track deal discussions, and get context from your team communications.',
              color: 'from-purple-500 to-pink-500',
            },
            {
              icon: (
                <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
              ),
              title: 'Calendar Sync',
              description: 'See meeting history, upcoming calls, and activity timelines alongside your deals.',
              color: 'from-blue-500 to-cyan-500',
            },
          ].map((feature, i) => (
            <div
              key={i}
              className="group p-6 rounded-xl border border-surface-800 bg-surface-900/30 hover:bg-surface-900/50 transition-all hover:border-surface-700"
            >
              <div className={`w-12 h-12 rounded-lg bg-gradient-to-br ${feature.color} flex items-center justify-center text-white mb-4`}>
                {feature.icon}
              </div>
              <h3 className="text-lg font-semibold text-surface-100 mb-2">{feature.title}</h3>
              <p className="text-surface-400">{feature.description}</p>
            </div>
          ))}
        </div>
      </section>

      {/* How it Works */}
      <section className="relative z-10 max-w-7xl mx-auto px-6 py-24 border-t border-surface-800">
        <h2 className="text-3xl font-bold text-surface-50 text-center mb-16">
          Get started in minutes
        </h2>

        <div className="grid md:grid-cols-3 gap-8">
          {[
            { step: '1', title: 'Connect your tools', desc: 'Link HubSpot, Slack, and Google Calendar with secure OAuth.' },
            { step: '2', title: 'Ask anything', desc: 'Query your data using natural language. No SQL or dashboards needed.' },
            { step: '3', title: 'Get insights', desc: 'Receive instant answers, reports, and actionable recommendations.' },
          ].map((item, i) => (
            <div key={i} className="text-center">
              <div className="w-12 h-12 rounded-full bg-primary-500/20 border border-primary-500/30 flex items-center justify-center text-primary-400 text-xl font-bold mx-auto mb-4">
                {item.step}
              </div>
              <h3 className="text-lg font-semibold text-surface-100 mb-2">{item.title}</h3>
              <p className="text-surface-400">{item.desc}</p>
            </div>
          ))}
        </div>

        <div className="text-center mt-16">
          <button
            onClick={onGetStarted}
            className="btn-primary text-lg px-8 py-4"
          >
            Start Free Trial
          </button>
          <p className="text-sm text-surface-500 mt-4">No credit card required</p>
        </div>
      </section>

      {/* Footer */}
      <footer className="relative z-10 border-t border-surface-800 py-8">
        <div className="max-w-7xl mx-auto px-6 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
              <svg className="w-3 h-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
              </svg>
            </div>
            <span className="text-surface-400 text-sm">© 2025 Revtops. All rights reserved.</span>
          </div>
          <div className="flex gap-6 text-sm text-surface-500">
            <a href="#" className="hover:text-surface-300 transition-colors">Privacy</a>
            <a href="#" className="hover:text-surface-300 transition-colors">Terms</a>
            <a href="#" className="hover:text-surface-300 transition-colors">Contact</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
