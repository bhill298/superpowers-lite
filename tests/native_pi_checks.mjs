// Companion to native_pi_checks.py; invoke through the Python isolation driver.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const [packageDir, cwd, agentDir, installerVersion] = process.argv.slice(2);
const sdk = await import(pathToFileURL(join(packageDir, 'dist/index.js')).href);
const { AuthStorage, ModelRegistry, DefaultResourceLoader, SettingsManager,
        SessionManager, createAgentSession } = sdk;
const model = {
    id: 'offline-test', name: 'Offline fixture', provider: 'lite-offline',
    api: 'openai-completions', baseUrl: 'http://127.0.0.1:1',
    reasoning: false, input: ['text'], contextWindow: 100000, maxTokens: 1000,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
};
const marker = `<!-- superpowers-lite:${installerVersion}:pi:`;
const report = { version: JSON.parse(readFileSync(join(packageDir, 'package.json'))).version };

// A synthetic extension proves that a delegation tool can coexist with Lite.
// It neither starts children nor claims to validate third-party extensions.
const delegationExtension = (pi) => {
    pi.registerTool({
        name: 'subagent', label: 'Fixture subagent',
        description: 'Offline fixture: dispatch an isolated worker with agent and task.',
        parameters: { type: 'object', properties: { agent: { type: 'string' }, task: { type: 'string' } },
                      required: ['agent', 'task'] },
        async execute() { throw new Error('Fixture tool must not execute'); },
    });
};

for (const withExtension of [false, true]) {
    const settingsManager = SettingsManager.inMemory({ enableSkillCommands: false });
    const loader = new DefaultResourceLoader({
        cwd, agentDir, settingsManager, noExtensions: true,
        noPromptTemplates: true, noThemes: true,
        extensionFactories: withExtension ? [delegationExtension] : [],
    });
    await loader.reload();
    const discovered = loader.getSkills();
    assert.equal(discovered.diagnostics.length, 0, JSON.stringify(discovered.diagnostics));
    const ours = discovered.skills.filter((s) => s.name.startsWith('superpowers-'));
    assert.equal(ours.length, 3);
    assert(ours.every((s) => s.disableModelInvocation));
    assert(ours.every((s) => s.filePath.startsWith(join(agentDir, 'skills'))));
    const authStorage = AuthStorage.inMemory();
    authStorage.setRuntimeApiKey('lite-offline', 'fixture-only');
    const { session } = await createAgentSession({
        cwd, agentDir, resourceLoader: loader, settingsManager,
        sessionManager: SessionManager.inMemory(cwd), authStorage,
        modelRegistry: ModelRegistry.inMemory(authStorage), model,
    });
    try {
        const captured = [];
        // Replace only model transport: Pi still builds prompts and expands
        // skill commands through its normal AgentSession.prompt path.
        session.agent.streamFn = (_model, context) => {
            captured.push(JSON.parse(JSON.stringify(context)));
            const message = {
                role: 'assistant', content: [{ type: 'text', text: 'fixture done' }],
                api: model.api, provider: model.provider, model: model.id,
                usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
                         cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } },
                stopReason: 'stop', timestamp: Date.now(),
            };
            return {
                async *[Symbol.asyncIterator]() { yield { type: 'done', reason: 'stop', message }; },
                async result() { return message; },
            };
        };
        await session.prompt('Explain a basic function.');
        assert.equal(captured.length, 1, JSON.stringify(session.agent.state.messages));
        const ordinary = captured[0];
        assert(!JSON.stringify(ordinary).includes(marker));
        assert(!ordinary.systemPrompt.includes('superpowers-brainstorming'));
        assert(ordinary.systemPrompt.includes('Superpowers Lite: explicitly started workflows'));
        assert(ordinary.systemPrompt.includes('FIXTURE_PI_RULE'));
        assert.equal(ordinary.tools.some((t) => t.name === 'subagent'), withExtension);
        await session.prompt('/skill:superpowers-brainstorming Design a fixture widget.');
        assert.equal(captured.length, 2);
        const explicit = JSON.stringify(captured[1].messages);
        assert(explicit.includes(marker));
        assert(explicit.includes('/tools/pi.md'));
        assert(explicit.includes('Design a fixture widget.'));
        assert(explicit.includes('authorizes chaining'));
        report[withExtension ? 'with_delegation_extension' : 'without_delegation_extension'] = {
            manual_skills: ours.length, automatic_advertising: false,
            global_rules_loaded: true, explicit_expansion: true,
            subagent_tool_present: withExtension,
        };
    } finally {
        session.dispose();
    }
}
console.log(JSON.stringify(report));
