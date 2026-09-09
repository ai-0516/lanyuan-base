function loadComponent(modulePath, propertyValues = {}) {
  let definition
  global.Component = options => {
    definition = options
  }

  jest.isolateModules(() => {
    require(modulePath)
  })

  if (!definition) {
    throw new Error(`Component was not registered by ${modulePath}`)
  }

  const defaults = Object.fromEntries(
    Object.entries(definition.properties || {}).map(([name, options]) => [name, options.value]),
  )
  const component = {
    data: { ...(definition.data || {}), ...defaults, ...propertyValues },
    setData(update) {
      Object.assign(this.data, update)
    },
    triggerEvent: jest.fn(),
  }

  for (const [name, method] of Object.entries(definition.methods || {})) {
    component[name] = method.bind(component)
  }

  return component
}

module.exports = { loadComponent }
