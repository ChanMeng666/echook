// echook view sensor: tells the status line which transcript is on screen.
//
// Claude Code pipes the main conversation to a status line script whatever the
// person is viewing, and a switch of view reaches no command hook. This is the
// one place the view can be read, so the module does that and nothing else: it
// draws nothing, and writes one small file that bin/audio-hooks-statusline.py
// reads. Why it is a module and how it was measured: docs/STATUS_LINE.md.

import type { Register } from 'claude-code'

// The status line runs with no CLAUDE_PLUGIN_DATA, so the two sides meet in the
// temp directory. _viewed_agent_file() in the status line spells the same path.
async function sidecarPath($, sessionId) {
  const tmp = (await $.env.get('TEMP')) || (await $.env.get('TMP')) || (await $.env.get('TMPDIR')) || '/tmp'

  return `${tmp}/claude_audio_hooks_queue/viewed_agent.${sessionId}.json`
}

async function publish($, agentId) {
  const agents = agentId === undefined ? [] : await $.agent.list()
  const agent = agents.find(candidate => candidate.id === agentId)
  const path = await sidecarPath($, await $.session.id())

  await $.fs.write(path, JSON.stringify({ agent_id: agentId ?? null, name: agent?.name, type: agent?.type }))
}

// A session resumed or reloaded on an agent's view must not keep showing it.
async function clear($) {
  const path = await sidecarPath($, await $.session.id())

  if (await $.fs.exists(path)) {
    await $.fs.write(path, JSON.stringify({ agent_id: null }))
  }
}

export const register: Register = on => {
  let published: string | undefined

  on('session.start', async ($, e, next) => {
    published = undefined
    await clear($).catch(() => undefined)

    return next(e)
  })

  // A switch of view re-runs this hook; every other render finds nothing to do.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    // No `view` on a build older than the prop: stays on the main conversation.
    const agentId = e.props.view?.agentId

    if (agentId !== published) {
      published = agentId
      await publish($, agentId).catch(() => undefined)
    }

    return next(e)
  })
}
