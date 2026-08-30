import type { LucideIcon } from "lucide-react";
export interface Translations {
  // Locale meta
  locale: {
    localName: string;
  };

  // Common
  common: {
    home: string;
    settings: string;
    delete: string;
    edit: string;
    rename: string;
    share: string;
    openInNewWindow: string;
    close: string;
    back: string;
    more: string;
    search: string;
    download: string;
    thinking: string;
    artifacts: string;
    public: string;
    custom: string;
    notAvailableInDemoMode: string;
    loading: string;
    error: string;
    copy: string;
    copied: string;
    version: string;
    lastUpdated: string;
    code: string;
    preview: string;
    cancel: string;
    save: string;
    install: string;
    create: string;
    import: string;
    export: string;
    exportAsMarkdown: string;
    exportAsJSON: string;
    exportSuccess: string;
    model: string;
    other: string;
    unlink: string;
    guest: string;
    confirm: string;
    stop: string;
    step: string;
    revert: string;
    review: string;
    fileSizeB: string;
    fileSizeKB: string;
    fileSizeMB: string;
    timeAgo: (value: number, unit: string) => string;
    stubResponseTitle: string;
    stubResponseDescription: (method: string, path: string) => string;
    openSidebarMenu: string;
    loadingWorkspace: string;
  };

  home: {
    docs: string;
    blog: string;
  };

  // Welcome

  // Echo OS landing page
  landing: {
    tagline: string;
    subtitle: string;
    getStarted: string;
    clickToEnter: string;
    capabilitiesPanel: string;
    features: {
      deepResearch: string;
      multiAgent: string;
      skillsTools: string;
      sandbox: string;
      memory: string;
      multiChannel: string;
    };
  };

  welcome: {
    greeting: string;
    description: string;
    echoTagline: string;
    createYourOwnSkill: string;
    createYourOwnSkillDescription: string;
    scenes: {
      daily: string;
      code: string;
      design: string;
      data: string;
      doc: string;
      agent: string;
    };
  };

  // Clipboard
  clipboard: {
    copyToClipboard: string;
    copiedToClipboard: string;
    failedToCopyToClipboard: string;
    linkCopied: string;
  };

  // Input Box
  inputBox: {
    placeholder: string;
    mode: string;
    flashMode: string;
    flashModeDescription: string;
    chatModeDescription: string;
    reactMode: string;
    reactModeDescription: string;
    ephemeralMode: string;
    ephemeralModeDescription: string;
    reasoningMode: string;
    reasoningModeDescription: string;
    reasoningEffort: string;
    reasoningEffortOff: string;
    reasoningEffortMinimal: string;
    reasoningEffortMinimalDescription: string;
    reasoningEffortLow: string;
    reasoningEffortLowDescription: string;
    reasoningEffortMedium: string;
    reasoningEffortMediumDescription: string;
    reasoningEffortHigh: string;
    reasoningEffortHighDescription: string;
    reasoningEffortXHigh: string;
    reasoningEffortMax: string;
    reasoningEffortCurrent: (label: string) => string;
    reasoningEffortMapped: (current: string, effective: string) => string;
    searchModels: string;
    surpriseMe: string;
    surpriseMePrompt: string;
    followupLoading: string;
    followupConfirmTitle: string;
    followupConfirmDescription: string;
    followupConfirmAppend: string;
    followupConfirmReplace: string;
    suggestions: {
      suggestion: string;
      prompt: string;
      icon: LucideIcon;
    }[];
    suggestionsCreate: (
      | {
          suggestion: string;
          prompt: string;
          icon: LucideIcon;
        }
      | {
          type: "separator";
        }
    )[];
  };

  // Message display
  message: {
    thinking: string;
    thinkingProcess: string;
    replyThinking: string;
    replyThinkingDescription: string;
    executionProcess: string;
    executionProcessDescription: string;
    visibleReasoning: string;
    executionSteps: string;
    toolCalls: string;
    todoAndContext: string;
    expandable: string;
    agentCluster: string;
    agentProgressSummary: (
      total: number,
      completed: number,
      errors: number,
    ) => string;
    processDetails: string;
    completedSteps: (n: number) => string;
    completedThings: (n: number) => string;
    statusViewing: string;
    statusCompleted: string;
    statusError: string;
    statusWaiting: string;
    assistant: string;
    turnLabel: (n: number) => string;
    turnNumberLabel: (n: number, label: string) => string;
    turnLocator: string;
    jumpToFirstTurn: string;
    jumpToLastTurn: string;
    backToLatest: string;
    latest: string;
    newUpdates: (n: number) => string;
    timeoutWarning: (seconds: number) => string;
    thinkingPlan: string;
    thinkingPlanSourceCheck: string;
    thinkingPlanCoworkFit: string;
    agent: string;
    noTaskDescription: string;
    processRecords: (n: number) => string;
    showMoreAgents: (n: number) => string;
    collapseAgents: string;
    viewReport: string;
    viewReportError: string;
    collapseReport: string;
    latestTool: string;
    execution: string;
    verification: string;
    process: string;
    actionCount: (n: number) => string;
    checkCount: (n: number) => string;
    reviewSelf: string;
    reviewTeam: string;
    reviewSecurity: string;
    artifactsCreated: (n: number) => string;
    artifactsCreatedAndFilesEdited: (created: number, edited: number) => string;
    filesEdited: (n: number) => string;
    revertSuccess: (n: number) => string;
    revertFailed: string;
    reviewAssigned: (label: string) => string;
    taskCompleted: string;
    taskFailed: string;
    previousAttemptRecovered: string;
    taskOutputs: string;
    viewProcess: string;
    makeSimilar: string;
    makeSimilarHint: string;
    retryTask: string;
    retryTaskHint: string;
    taskFailedReason: string;
    resultUrl: string;
    openResult: string;
    verificationRan: string;
    verificationPassed: string;
    verificationFailed: string;
    testsPassed: string;
    lintClean: string;
    buildSucceeded: string;
    completedChanges: string;
    viewComputer: string;
    artifactsSummary: string;
    changesSummary: string;
    auditActions: string;
    assignReviewTo: string;
    moreFiles: (n: number) => string;
    downloadStillInArtifactsPanel: string;
    diffTruncated: string;
    diffTruncatedTooltip: string;
    hunkReverted: string;
    hunkRevertFailed: string;
    accept: string;
    reject: string;
    accepted: string;
    rejected: string;
    useTopicInAgent: string;
    actionLabel: (action: string) => string;
    attachmentFallback: string;
    imageAttachment: string;
    attachmentPreview: string;
    removeAttachment: string;
    previousBranch: string;
    nextBranch: string;
    branchPosition: (current: number, total: number) => string;
    grounding: {
      /** Describe auto-prefetched context separately from explicit tool work. */
      summary: (first: string, total: number) => string;
    };
    thinkingForSeconds: (seconds: number) => string;
    planningNSteps: (n: number) => string;
    fileOperationsCount: (n: number) => string;
    fileOperationsCountWithDiff: (
      n: number,
      added: number,
      removed: number,
    ) => string;
    toolCallsCount: (n: number) => string;
    diffLinesHidden: (n: number) => string;
    loadOlderTurns: string;
    loadingOlderTurns: string;
  };

  // Execution Checklist
  executionChecklist: {
    title: string;
    clarifyGoal: string;
    clarifyGoalDetail: string;
    searchRound: (round: number, query: string) => string;
    adjustKeywords: (round: number) => string;
    adjustKeywordsDetail: string;
    webSearch: (count: number) => string;
    readContext: string;
    writeFile: string;
    runCommand: string;
    callTool: (count: number) => string;
    toolCallDetail: string;
    analyzeAndAlign: string;
    analyzeAndAlignDetail: string;
    generateResponse: string;
    generateResponseDetail: string;
    // Search classification
    marketSize: string;
    competition: string;
    technology: string;
    consumerDemand: string;
    evidenceRound: (round: number) => string;
    queryPrefix: string;
    continueFromPrevious: string;
  };

  // Chat Input Box
  chatInputBox: {
    quickCapabilities: string;
    collaborators: string;
    collaboratorsSingle: string;
    collaboratorsCountUnit: string;
    collaboratorsHelp: string;
    collaboratorsSearchPlaceholder: string;
    collaboratorsTaskFallback: string;
    collaboratorsCoreGroup: string;
    collaboratorsOnDemandGroup: string;
    collaboratorsOnDemandBadge: string;
    collaboratorsOnDemandHint: string;
    responseMode: string;
    responseModeTeamRequired: string;
    groupTaskTools: string;
    groupTaskStart: string;
    groupTaskAddContent: string;
    groupTaskAuto: string;
    groupTaskBuild: string;
    groupTaskResearch: string;
    groupTaskDevelop: string;
    groupTaskAudit: string;
    groupTaskUxui: string;
    groupTaskActive: string;
    groupTaskClear: string;
    projectFiles: string;
    removeCapability: (name: string) => string;
    uploadImages: string;
    workspaceFiles: (name: string) => string;
    noWorkspaceFiles: string;
    uploadDeviceFiles: string;
    commands: string;
    plugins: string;
    availablePlugins: string;
    capabilityLoadFailed: string;
    noAvailablePlugins: string;
    managePlugins: string;
    explorePlugins: string;
    skills: string;
    searchSkills: string;
    noAvailableSkills: string;
    manageSkills: string;
    addResearchMaterial: string;
    codexPlan: string;
    codexSpec: string;
    codexGoal: string;
    composerInsertions: string;
    insertCodexPlan: string;
    insertCodexSpec: string;
    insertCodexGoal: string;
    insertBrowserSurface: string;
    insertChromeSurface: string;
    workflow: string;
    deepResearchConfig: string;
    roles: string;
    materials: string;
    collapse: string;
    materialNote: string;
    url: string;
    file: string;
    text: string;
    textTitle: string;
    pasteTextMaterial: string;
    focus: string;
    deliverable: string;
    searchAngles: string;
    customRole: string;
    addRole: string;
    removeRole: string;
    toggleMaterial: string;
    removeMaterial: string;
    startThreadBeforeUpload: string;
    uploadFailed: string;
    attachmentReadFailed: string;
    maxSubagents: string;
    maxSearches: string;
    permissionModeLabel: string;
    permissionModeDefault: string;
    permissionModeDefaultDesc: string;
    permissionModeAcceptEdits: string;
    permissionModeAcceptEditsDesc: string;
    permissionModeBypass: string;
    permissionModeBypassDesc: string;
    permissionModeBypassConfirmTitle: string;
    permissionModeBypassConfirmDesc: string;
    permissionModeBypassConfirmAction: string;
    permissionModePlan: string;
    permissionModePlanDesc: string;
    seedWorkflow: string;
    send: string;
    stop: string;
    projectModeLabel: string;
    projectModeHint: string;
    projectStatusTitle: string;
    projectStatusDescUnlocked: string;
    projectStatusDescLocked: string;
    projectWriteAccess: string;
    projectReadOnly: string;
    permissionFullAccess: string;
    permissionAcceptEdits: string;
    permissionConfirm: string;
    addImage: string;
    addAppshot: string;
    capturingAppshot: string;
    appshotHint: string;
    appshotSource: string;
    appshotFailed: string;
    windowTools: string;
    automationTarget: string;
    chooseAutomationTarget: string;
    currentChromeTab: string;
    currentDesktopWindow: string;
    loadingAutomationTargets: string;
    noAutomationTargets: string;
    clearAutomationTarget: string;
    automationOnline: string;
    automationOffline: string;
    automationReconnecting: string;
    automationDesktop: string;
    automationIdle: string;
    automationRunning: string;
    automationPaused: string;
    automationPause: string;
    automationResume: string;
    automationTakeover: string;
    automationEvidence: string;
    automationNoEvidence: string;
    automationControlFailed: string;
    removeImage: string;
    readme: string;
  };

  // Cowork group: presence + unread + replayable search
  coworkCollab: {
    searchPlaceholder: string;
    noResults: string;
    online: string;
    members: string;
    unread: (n: number) => string;
    kindBlackboard: string;
    kindTask: string;
    kindEvent: string;
    kindRoomMessage: string;
    kindRoomTask: string;
    linkedRoom: string;
  };

  // Collaboration capability
  teamMode: {
    mode: string;
    modeTeam: string;
    modeTeamDesc: string;
    createTeam: string;
    chat: string;
    chatDescription: string;
    cowork: string;
    coworkDescription: string;
    comingSoon: string;
  };

  // Create Project Dialog
  createProjectDialog: {
    title: string;
    placeholder: string;
    quickCategory: string;
    categoryInvest: string;
    categoryHomework: string;
    categoryWriting: string;
    categoryTravel: string;
    aiMembersLabel: string;
    aiMembersDescription: string;
    aiMembersSelected: (count: number) => string;
    agentsLoading: string;
    agentsUnavailable: string;
    humanMembersLabel: string;
    humanMembersAfterCreate: string;
    humanMembersDescription: string;
    invitePeopleOnArrival: string;
    creatorRoleLabel: string;
    creatorRole: string;
    creatorRoleDescription: string;
    hint: string;
    cancel: string;
    create: string;
  };

  promoteProjectDialog: {
    trigger: string;
    title: string;
    description: string;
    nameLabel: string;
    namePlaceholder: string;
    goalLabel: string;
    goalPlaceholder: string;
    cancel: string;
    submit: string;
    submitting: string;
    success: string;
    failed: string;
  };

  projectCapability: {
    enabled: string;
    startPlan: string;
    openWorkbench: string;
    moreActions: string;
    detach: string;
    detachConfirmTitle: string;
    detachConfirmDescription: string;
    detachConfirmAction: string;
    forceDetachConfirmTitle: string;
    forceDetachConfirmDescription: string;
    forceDetachConfirmAction: string;
    detached: string;
    detachCancelled: string;
    detachFailed: string;
    detachBindingChanged: string;
    statusPlanning: string;
    statusRunning: string;
    statusBlocked: string;
    statusDone: string;
    statusFailed: string;
  };

  // Clarification Questionnaire
  clarificationQuestionnaire: {
    title: string;
    recommended: string;
    previous: string;
    cancel: string;
    continueLabel: string;
    completedHeader: string;
    continuePrompt: string;
  };

  // Dispatch Card (swarm)
  dispatchCard: {
    agentSwarmTitle: string;
    parallelTasks: (n: number) => string;
    done: string;
    researchFocus: string;
    equipment: string;
    personalityLabel: string;
    battleRecord: (tasks: number) => string;
    ratingSuffix: (stars: string) => string;
  };

  // Deliverables Card (swarm)
  deliverablesCard: {
    detailedReports: string;
    expertsSaved: (n: number) => string;
    colReport: string;
    colPath: string;
    colActions: string;
    copyPath: string;
    download: string;
    allFiles: string;
    previewOrDownload: string;
  };

  // Landing footer
  landingFooter: {
    tagline: string;
    productTitle: string;
    workspaceLink: string;
    aboutLink: string;
    workflowLink: string;
    resourcesTitle: string;
    skillMarketLink: string;
    communityTitle: string;
    wechat: string;
  };

  // Message grouping (activity label bag passed to pure module)
  messageGrouping: {
    fileFallback: string;
    writeFile: (file: string) => string;
    writeFileWithLines: (file: string, added: number) => string;
    editFile: (file: string) => string;
    editFileAddRemove: (file: string, added: number, removed: number) => string;
    editFileAdded: (file: string, added: number) => string;
    editFileRemoved: (file: string, removed: number) => string;
    executeCommand: string;
    executeCommandWith: (cmd: string) => string;
    planStep: string;
    think: string;
    thinking: string;
    deepThinking: string;
    thoughtFor: string;
    hideProcessReplay: string;
    processReplay: string;
    replayNSteps: (n: number) => string;
    hideSavedSteps: string;
    viewProcessSummary: string;
    viewNSavedSteps: (n: number) => string;
    countItems: (n: number) => string;
    liveProcess: string;
    liveProcessRunning: string;
    liveProcessWaiting: string;
    liveProcessError: string;
    liveProcessDone: string;
    liveProcessPending: string;
    liveProcessHistory: (n: number) => string;
    reasoningFallback: string;
    callTeammate: string;
    searchSources: string;
    readWebpage: string;
    readFile: string;
    updateFile: string;
    runAction: string;
    teammateTimeout: string;
    factSummaryPath: (value: string) => string;
    factSummaryCount: (value: string) => string;
    factSummaryStatus: (value: string) => string;
    factSummaryTitle: (value: string) => string;
    factSummaryText: (value: string) => string;
    factSummaryDuration: (value: string) => string;
    factSummaryLines: (value: string) => string;
    factSummaryMatches: (value: string) => string;
    factSummarySucceeded: string;
    factSummaryFailed: string;
    factSummaryExitCode: (value: string) => string;
    effectNeedsReview: string;
    capabilityDisabled: (toolName: string) => string;
    enableCapability: string;
    enablingCapability: string;
    actionLabels: {
      createFile: string;
      editFile: string;
      searchFiles: string;
      viewDirectory: string;
      readFile: string;
      runCommand: string;
      searchWeb: string;
      browseWeb: string;
      browserClick: string;
      browserType: string;
      browserScreenshot: string;
      browserNavigate: string;
      browserAction: string;
      updatePlan: string;
      useCapability: string;
      delegateTask: string;
      submitResult: string;
      deleteFile: string;
      moveFile: string;
      startPreview: string;
      networkRequest: string;
      aggregateFileWrite: (count: number) => string;
      aggregateFileRead: (count: number) => string;
      aggregateCommand: (count: number) => string;
      aggregateWebSearch: (count: number) => string;
      aggregateBrowser: (count: number) => string;
      aggregateTeammate: (count: number) => string;
      aggregateTodo: (count: number) => string;
      aggregateOther: (count: number) => string;
    };
    thinkingDuration: (value: string) => string;
    thinkingProcess: string;
  };

  // Trace generator (swarm simulation label bag)
  traceGenerator: {
    searchTitle: (query: string) => string;
    resultCount: (n: number) => string;
    toolCallTitle: string;
    toolCallDetail: string;
    milestoneSave: (bucket: number, total: number) => string;
    thinkThoughts: string[];
  };

  // Collaboration picker strings
  createTeamDialog: {
    title: string;
    description: string;
    allAgents: (n: number) => string;
    selected: (n: number, max: number) => string;
    memberCounter: (n: number, max: number) => string;
    selectMembersTitle: string;
    memberLimitReached: string;
    searchAgentsPlaceholder: string;
    selectedBadge: string;
    noMatches: string;
    clearSelected: string;
    membersLabel: string;
    leaderLabel: string;
    leaderUnset: string;
    emptyHintL1: string;
    emptyHintL2: string;
    currentTl: string;
    setAsTl: string;
    teamNameLabel: string;
    teamNamePlaceholder: string;
    create: string;
    cancel: string;
    roleModels: {
      title: string;
      description: string;
      customCount: (n: number) => string;
      defaultPrefix: string;
      help: string;
      tiers: Record<"default" | "cheap" | "primary", string>;
      roles: Record<
        | "planner"
        | "generator"
        | "synthesizer"
        | "researcher"
        | "critic"
        | "evaluator"
        | "reviewer"
        | "fact_checker"
        | "verifier"
        | "arbiter",
        string
      >;
    };
  };

  // Code Mode
  codeMode: {
    ask: string;
    plan: string;
    agent: string;
    solo: string;
    sandbox: string;
    fullAccess: string;
    workspaceDir: string;
    selectWorkspace: string;
    chooseWorkspaceFolder: string;
    personalSpace: string;
    selectFolder: string;
    selectWorkspaceFolderFirst: string;
    recentWorkspaces: string;
    noRecentWorkspaces: string;
    browseCurrentFolder: string;
    chooseCurrentFolder: string;
    selectThisFolder: string;
    folderPickerPathUnavailable: string;
    openFolderCta: string;
    cloneRepoCta: string;
    connectSshCta: string;
    cloneRepoUnavailable: string;
    connectSshUnavailable: string;
    parentFolder: string;
    loadingFolders: string;
    noSubfolders: string;
    explorer: string;
    wiki: string;
    builder: string;
    coder: string;
    builderDesc: string;
    coderDesc: string;
    autoDetected: string;
    searchConversations: string;
    showFileTree: string;
    hideFileTree: string;
    inputPlaceholder: string;
    running: string;
    defaultModel: string;
    files: string;
    cloudServices: string;
    quickActionNewFile: string;
    quickActionAttachFile: string;
    quickActionOpenMcp: string;
    fast: string;
    deep: string;
    swarms: string;
    // Capability selector (collab / auto / swarms)
    capabilityCollab: string;
    capabilityAuto: string;
    capabilityCollabTitle: string;
    capabilityAutoTitle: string;
    capabilitySwarmsTitle: string;
    advanced: string;
    architectLevelTooltip: string;
    architectLevelL0: string;
    architectLevelL1: string;
    architectLevelL2: string;
    architectLevelL3: string;
    // Right-pane toggle + extended rail
    codeTab: string;
    previewTab: string;
    showExtendedPanels: string;
    hideExtendedPanels: string;
    moreActions: string;
    subtaskSwarmTitle: string;
    // Fast-mode status badges
    fastOver60s: string;
    fastRunning: string;
    fastCompleted: string;
    fastProgress: (sec: number) => string;
    fastDone: (sec: number) => string;
    hardTimeoutOn: string;
    hardTimeoutOff: string;
    loadingInline: string;
    // Auto verify
    autoVerifyPassed: string;
    autoVerifyFailed: string;
    autoVerifyFailedQueued: string;
    autoVerifyRunning: string;
    changesAwaitingVerify: string;
    verify: string;
    attemptCompleted: (
      attempt: number,
      count: number,
      singular: boolean,
    ) => string;
    autoFixLimitReached: (attempt: number, max: number) => string;
    failedChecks: (count: number, singular: boolean) => string;
    changedFilesPending: (count: number, singular: boolean) => string;
    runningProjectChecks: string;
    // Panel labels
    term: string;
    terminal: string;
    hideTerminal: string;
    showTerminal: string;
    hidePreviewSidecar: string;
    showPreviewSidecar: string;
    debug: string;
    closeSidecar: string;
    // Misc
    goToLinePrompt: string;
    goToLineDefault: string;
    workbenchLayoutPanels: string;
    verificationFailedNoDetails: string;
    // Terminal input
    terminalPlaceholder: string;
    terminalRestart: string;
    terminalClose: string;
    terminalConnectionFailed: string;
    terminalConnectedHint: string;
    terminalConnecting: string;
    stop: string;
    send: string;
    // File tree
    refresh: string;
    // Live preview
    previewDiagnostic: string;
    previewDiagnostics: string;
    previewConsole: string;
    previewConsoleEmpty: string;
    previewConsoleClear: string;
    previewConsoleAddToChat: string;
    previewConsoleCount: (n: number) => string;
    previewDevTools: string;
    previewDevToolsUnavailable: string;
    // Tool panel (realtime)
    toolPanelPreview: string;
    toolPanelTerminal: string;
    // Threads sidebar (realtime)
    threadsHistory: string;
    newThread: string;
    searchThreads: string;
    noThreadsYet: string;
    // Verify panel
    runChecks: string;
    autoVerifyAttempt: (attempt: number) => string;
    queuedAutoFix: (count: number, max: number) => string;
    latestTurnPassed: string;
    noAutoFixQueued: string;
    moreFiles: (count: number) => string;
    clickRunChecksToVerify: string;
    runningVerification: string;
    projectLabel: string;
    sendErrorToAI: string;
    fix: string;
    copyOutput: string;
    // Changes panel
    hunks: (count: number) => string;
    revertToLastCommit: string;
    fileReverted: string;
    failedToRevertFile: string;
    hunkAccepted: string;
    hunkReverted: string;
    failedToRevertHunk: string;
    steps: {
      understandRequirement: string;
      analyzeIntent: string;
      architectureDesign: string;
      planStructure: string;
      generateCode: string;
      writeCode: string;
      codeReview: string;
      checkQuality: string;
      parallelDev: string;
      multiAgentCollab: string;
      frontendDev: string;
      backendDev: string;
      dbDesign: string;
    };
  };

  // Agent Workflow
  agentWorkflow: {
    title: string;
    running: string;
    progress: string;
    empty: string;
    showDetails: string;
    hideDetails: string;
    input: string;
    result: string;
  };

  // Realtime index page
  realtime: {
    title: string;
    subtitle: string;
    newThread: string;
    orResume: string;
    threadIdPlaceholder: string;
    open: string;
    recent: string;
    loadError: (error: string) => string;
    loading: string;
    empty: string;
    turns: (count: number) => string;
    lastStatus: (status: string) => string;
    updated: (date: string) => string;
    panelToggle: {
      close: string;
      open: string;
    };
    viewActions: string;
    finalArtifact: {
      generated: string;
      view: string;
    };
    recording: {
      recording: (stepCount: number) => string;
      idle: string;
    };
    replay: {
      titleDefault: string;
      footer: string;
    };
    composer: {
      legacyOnDemandContinued: string;
      legacyOnDemandAttachments: string;
      placeholderCode: string;
      placeholderNew: string;
      placeholderEcho: string;
    };
    recorder: {
      defaultName: string;
    };
  };

  // Realtime item views
  realtimeItems: {
    error: {
      label: string;
      willRetry: string;
      yes: string;
      no: string;
    };
    command: {
      cwd: string;
      waitingOutput: string;
      running: string;
      exitCode: (code: number) => string;
    };
    fileChange: {
      title: (count: number) => string;
      hunkCount: (count: number) => string;
      noDiff: string;
      accept: string;
      reject: string;
      operations: {
        create: string;
        delete: string;
        modify: string;
      };
      decisions: {
        accepted: string;
        rejected: string;
        pending: string;
      };
    };
    verification: {
      title: string;
      passed: string;
      failed: string;
      running: string;
      exitCode: (code: number) => string;
      relatedFiles: (count: number) => string;
      relatedChanges: (count: number) => string;
    };
    plan: {
      label: string;
    };
    reasoning: {
      label: string;
      empty: string;
    };
    todo: {
      label: string;
    };
  };

  // Quick Templates
  quickTemplates: {
    title: string;
    hint: string;
    items: {
      companyWebsite: { name: string; description: string; prompt: string };
      personalPortfolio: { name: string; description: string; prompt: string };
      ecommercePage: { name: string; description: string; prompt: string };
      dashboard: { name: string; description: string; prompt: string };
      crmSystem: { name: string; description: string; prompt: string };
      todoApp: { name: string; description: string; prompt: string };
      chatInterface: { name: string; description: string; prompt: string };
      searchPage: { name: string; description: string; prompt: string };
      apiDocs: { name: string; description: string; prompt: string };
      puzzleGame: { name: string; description: string; prompt: string };
    };
  };

  // Agent Workbench Panel (swarm)
  workspaceComputer: Record<string, string>;
  agentOperator: Record<string, string>;

  // Agent Workbench Panel (swarm)
  agentWorkbench: {
    idle: string;
    finished: string;
    running: string;
    newTaskTitle: string;
    closeTitle: string;
    emptyNoAgents: string;
    startNewTaskButton: string;
    composerHint: string;
    taskListView: string;
    computerView: string;
    executionView: string;
    reportView: string;
    traceFeedEmpty: string;
    liveEventStream: string;
    eventsCount: (count: number) => string;
    computerViewEmpty: string;
    browsingTrail: (emoji: string) => string;
    reportPending: string;
    agentRunning: string;
    durationSeconds: (s: string) => string;
    agentComputer: string;
    agentNames: string[];
    dimensionTask: (index: number) => string;
    waitingToStart: string;
    waitingForPhase: string;
    phaseCompleted: string;
    webSearchActions: (count: number) => string;
    fileActions: (count: number) => string;
    terminalActions: (count: number) => string;
    executionActions: (count: number) => string;
    listSeparator: string;
    statusProcessing: string;
    statusCompleted: string;
    statusError: string;
    executingTask: string;
    waitingToContinue: string;
    currentProgress: string;
    stepProgress: (current: number, total: number) => string;
    minimizeProgress: string;
    restoreProgress: string;
    closeWorkspace: string;
    activityTrace: string;
    computerViewLabel: string;
    computerViewHint: string;
    copyDetails: string;
    waitingForToolResult: string;
    kindTerminal: string;
    kindBrowser: string;
    kindSearch: string;
    kindRead: string;
    kindFile: string;
    kindTodos: string;
    kindAgent: string;
    request: string;
    response: string;
    phase: string;
    steps: string;
    messages: string;
    workingSet: string;
    resumePlan: string;
    copyMarkdown: string;
    copyJson: string;
    candidateRecoveryPoints: string;
    noResumeProposalsAvailable: string;
    iteration: (iteration: number) => string;
    stepCount: (count: number) => string;
    fileCount: (count: number) => string;
    reviewRecoveryPoint: string;
    review: string;
    resumeDraft: string;
    useAsDraft: string;
    resumeFromLatestCheckpoint: string;
    resumeFromPhase: (phase: string) => string;
    restorePhase: (phase: string) => string;
    restoreLatestPhase: string;
    continueFromIteration: (iteration: number) => string;
    rehydrateWorkingSetFiles: (count: number) => string;
    useLastProgressSummary: (progress: string) => string;
    reviewLatestProgressSummary: string;
    checkpointLabel: (type: string, id: number) => string;
    taskLabel: (taskId: string) => string;
    continueFromIterationLabel: (iteration: number) => string;
    phaseLabel: (phase: string) => string;
    workingSetFilesLabel: (count: number) => string;
    rawCheckpointExcluded: string;
    resumeDraftIntro: string;
    resumeTitleLabel: (title: string) => string;
    progressLabel: (progress: string) => string;
    safetyRequirements: string;
    useOnlySanitizedRecovery: string;
    doNotAssumeRawSnapshots: string;
    reconfirmBeforeDestructive: string;
    unknown: string;
  };

  // Agent workbench pages
  agentPhases: {
    planning: string;
    exploring: string;
    implementing: string;
    testing: string;
    deploying: string;
    genericPrepare: string;
    genericExecute: string;
    genericDeliver: string;
  };

  agentWorkbenchPages: {
    collapse: string;
    expandDetails: string;
    openArtifact: (title: string) => string;
    viewDiff: (title: string) => string;
    reference: {
      files: string;
      plans: string;
      web: string;
      memory: string;
      other: string;
    };
    statusRunning: string;
    statusWaitingApproval: string;
    statusError: string;
    statusDone: string;
    progress: string;
    currentObjective: string;
    currentObjectiveHint: string;
    resultReceipt: string;
    resultReceiptDescription: string;
    recoveredOperations: (count: number) => string;
    verifiedSteps: (count: number) => string;
    unresolvedSteps: (count: number) => string;
    noResultYet: string;
    thinkingDetail: string;
    thinkingInProgress: string;
    thinkingDone: string;
    executionDetail: string;
    roundTitle: (iteration: number) => string;
    roundActivitySummary: (actionCount: number, factCount: number) => string;
    artifacts: string;
    generatedArtifacts: string;
    changedFiles: string;
    subagents: string;
    subagentsCompleted: (done: number, total: number) => string;
    subagentsFailed: (count: number) => string;
    subagentsRunning: (count: number) => string;
    subagentsPending: (count: number) => string;
    failedLanes: (lanes: string) => string;
    inputs: string;
    inputsUploadedFiles: (count: number) => string;
    inputsAttachments: (count: number) => string;
    context: string;
    contextCompress: string;
    contextDescription: string;
    contextUsed: (percentage: number, limit: string) => string;
    observableThisRound: string;
    sourceCount: (count: number) => string;
    sourceCountWithLabel: (label: string, count: number) => string;
    estimatePercentage: (percentage: number) => string;
    noSources: string;
    estimatedTokens: (count: number) => string;
    noObservableReferences: string;
    dashboardOverview: string;
    dashboardOverviewDescription: string;
    noSubagentsObservedDescription: string;
    metricRunning: string;
    metricCompleted: string;
    metricError: string;
    waitingForTaskEvents: string;
    subagentRuntimeDetails: string;
    roleLabel: string;
    currentToolLabel: string;
    startTimeLabel: string;
    durationLabel: string;
    eventCountLabel: string;
    parentTaskLabel: string;
    latestThoughtLabel: string;
    resultSummaryLabel: string;
    blackboardWritesLabel: string;
    filesTouchedLabel: string;
    errorLabel: string;
    noneYet: string;
    eventsCount: (count: number) => string;
    agentClusterCreateAssistant: string;
    roleCard: string;
    backToRoleCard: string;
    roleDescription: string;
    noFullRoleDescription: string;
    defaultMotto: string;
    iterationRound: (count: number) => string;
    noWorkDirDescription: string;
    noDiffEntriesDescription: string;
    filesTab: string;
    diffTab: string;
    terminalTab: string;
    browserTab: string;
    projectTab: string;
  };

  // Agent workbench panel (kanban / screen timeline)
  agentWorkbenchPanel: {
    noOperationRecords: string;
    noCurrentOperation: string;
    processFrames: string;
    frameCount: (count: number) => string;
    frameLabel: (current: number, total: number) => string;
    currentFrameLabel: (current: number, total: number) => string;
    phaseStatusRunning: string;
    phaseStatusError: string;
    phaseStatusDone: string;
    phaseStatusPending: string;
    robot: string;
    noRunningRobotProcess: string;
    startingRobotProcess: string;
    locateTranscriptEvent: string;
    collapseWorkbench: string;
    tabList: string;
    summaryLabel: string;
    latestTurnContext: string;
    latestTurnContextDescription: string;
    agentStatusRunning: string;
    agentStatusError: string;
    agentStatusDone: string;
    agentStatusPending: string;
    mainComputer: string;
    filterByAgent: string;
    filterChipMain: string;
    mainController: string;
    subComputer: string;
    currentConversation: string;
    timelinePosition: (sequence: number) => string;
    workbenchSlots: string;
    viewMainAgentSlot: string;
    mainAgentProcessTitle: string;
    dockStatusRunning: string;
    dockStatusError: string;
    dockStatusDone: string;
    dockStatusPending: string;
    dockStatusPresent: string;
    collaboratorSeat: string;
    leaderSeat: string;
    collaboratorPresentDescription: (role: string) => string;
    noIndependentProcessActivity: string;
    noIndependentProcessActivityDescription: string;
    switchToMainComputer: string;
    viewAgentProcess: (label: string) => string;
    agentClusterIndependentProcess: string;
    subAgent: string;
    noTaskDescription: string;
    waitingForSubagentOutput: string;
    processReplay: string;
    subagentConversation: string;
    mainDelegatedTask: string;
    processRecords: (count: number) => string;
    iterationRounds: (count: number) => string;
    computerViewSubtitle: string;
    computerViewSelectHint: string;
    computerViewEmpty: string;
    computerViewEmptyDesc: string;
    visibilityPanelTitle: string;
    visibilityPanelAttention: string;
    visibilityPanelEmpty: string;
    visibilityStep: string;
    scrollToBottom: string;
    viewLatestProgress: string;
    subagentBusStreamTitle: string;
    subagentBusStreamLive: string;
    subagentBusStreamConnecting: string;
    subagentBusStreamError: string;
    subagentDispatchFailed: string;
    subagentBusStreamEmpty: string;
    subagentBusStreamEvents: (count: number) => string;
    substreamTab: string;
  };

  // Diagnostics page
  diagnosticsPage: {
    title: string;
    description: string;
    tabs: {
      runtime: string;
      streaming: string;
      featureFlags: string;
      suggestions: string;
      remote: string;
      invariants: string;
    };
    noActiveProject: string;
    streaming: {
      title: string;
      description: string;
      clear: string;
      empty: string;
      samples: string;
      ttftP50: string;
      ttftP95: string;
      maxGapP95: string;
      stalledRate: string;
      unsuccessfulRate: string;
      time: string;
      outcome: string;
      maxGap: string;
      duration: string;
      endState: string;
      stalled: string;
      normal: string;
      outcomes: {
        completed: string;
        paused: string;
        cancelled: string;
        interrupted: string;
        failed: string;
      };
    };
  };

  // Runtime self-check panel
  runtimeSelfCheckPanel: {
    title: string;
    ready: string;
    degraded: string;
    blocked: string;
    notReported: string;
    refresh: string;
    refreshing: string;
    refreshAria: string;
    loading: string;
    loadFailed: (error: string) => string;
    status: string;
    runtimeVersion: string;
    generatedAt: string;
    versions: string;
    runtime: string;
    pyproject: string;
    frontendPackage: string;
    process: string;
    pid: string;
    python: string;
    cwd: string;
    argv: string;
    frontend: string;
    clientBackendBaseUrl: string;
    sameOriginBackend: string;
    selfCheckEndpoint: string;
    observedOrigin: string;
    canonicalOrigin: string;
    proxyTarget: string;
    proxyMatchesBackend: string;
    webui: string;
    webuiAvailable: string;
    webuiDist: string;
    webuiEnvDist: string;
    webuiAssets: string;
    webuiEnvInvalid: string;
    webuiDevFallback: string;
    modelCompat: string;
    compatProfiles: string;
    domesticProfiles: string;
    requiredProfilesPresent: string;
    missingProfiles: string;
    profileIds: string;
    apiSurface: string;
    routeCount: string;
    requiredRoutesPresent: string;
    missingRoutes: string;
    capabilitySurfaces: string;
    surface: string;
    capabilities: string;
    missing: string;
    orchestration: string;
    runEvidence: string;
    automation: string;
    backend: string;
    canonicalBaseUrl: string;
    requestOrigin: string;
    host: string;
    port: string;
    loopbackAliases: string;
    checks: string;
    warnings: string;
    nextActions: string;
    passed: string;
    failed: string;
    empty: string;
  };

  // Feature Flags panel
  featureFlagsPanel: {
    title: string;
    reload: string;
    reloading: string;
    reloadAria: string;
    loading: string;
    loadFailed: (error: string) => string;
    empty: string;
    experimental: string;
  };

  // Ambient Suggestions panel
  ambientSuggestionsPanel: {
    title: string;
    generate: string;
    generating: string;
    generateAria: string;
    loading: string;
    loadFailed: (error: string) => string;
    featureDisabled: string;
    empty: string;
    emptyGenerateHint: string;
    accept: string;
    dismiss: string;
    acceptAria: (title: string) => string;
    dismissAria: (title: string) => string;
    recent: (count: number) => string;
    dismissed: (count: number) => string;
    experimental: string;
  };

  // Follow-up suggestions (contextual bubbles)
  followUpSuggestions: {
    title: string;
    selectAria: (title: string) => string;
    dismissAria: (title: string) => string;
  };

  // Remote Backends panel
  remoteBackendsPanel: {
    title: string;
    disabled: string;
    disabledAria: string;
    loadFailed: (error: string) => string;
    addBackendAria: string;
    addFailed: string;
    namePlaceholder: string;
    nameAria: string;
    urlPlaceholder: string;
    urlAria: string;
    add: string;
    adding: string;
    loading: string;
    empty: string;
    untested: string;
    reachable: string;
    error: string;
    ping: string;
    pinging: string;
    remove: string;
    removing: string;
    pingAria: (name: string) => string;
    removeAria: (name: string) => string;
    removeConfirmTitle: (name: string) => string;
    removeConfirmDescription: string;
  };

  // Invariants panel
  invariantsPanel: {
    title: string;
    ruleCount: (count: number) => string;
    ruleCountAria: string;
    enforcerCount: (count: number) => string;
    enforcerCountAria: string;
    rebuild: string;
    rebuilding: string;
    rebuildAria: string;
    loadFailed: (error: string) => string;
    filterPlaceholder: string;
    filterAria: string;
    loading: string;
    emptyFiltered: (filter: string) => string;
    empty: string;
    enforcerCountLabel: (count: number) => string;
  };

  // Plugins page
  plugins: {
    pageLoading: string;
    pageTitle: string;
    pageSubtitle: string;
    tabSkillMarket: string;
    refreshButton: string;
    feature1Title: string;
    feature1Desc: string;
    feature2Title: string;
    feature2Desc: string;
    statTotal: string;
    statEnabled: string;
    statErrors: string;
    statCapabilities: string;
    cardAuthor: string;
    cardStatus: string;
    cardCapabilities: string;
    statusEnabled: string;
    statusDisabled: string;
    emptyTitle: string;
    emptyHint: string;
    searchPlaceholder: string;
    filterAllAuthors: string;
    filterByAuthor: (author: string) => string;
    statusAll: string;
    statusEnabledFilter: string;
    statusDisabledFilter: string;
    noMatches: string;
    tryDifferentQuery: string;
    configureTitle: (name: string) => string;
    configureDescription: (name: string) => string;
    configureNoConfig: string;
    configureCancel: string;
    configureSave: string;
    configureSaving: string;
    statusEnabledTooltip: string;
    statusDisabledTooltip: string;
    statusErrorTooltip: string;
    badgeSkill: string;
    badgeChannel: string;
    badgeConfig: string;
    badgeCommand: string;
    badgeCapability: string;
    statusError: string;
    configure: string;
    configureAria: (name: string) => string;
    backToWorkspace: string;
    registryTitle: string;
    registryDescription: string;
    registryInstallable: (count: number) => string;
    registryInstallAria: (id: string) => string;
    registryInstalling: string;
    registryUpgrade: string;
    registryUpToDate: string;
    registryInstalledMessage: (id: string, version: string) => string;
    surfaceFallback: string;
    discoveredTitle: string;
    discoveredHint: string;
    noDiscovered: string;
    refreshDiscovered: string;
    actionLoad: string;
    actionStart: string;
    actionStop: string;
    actionUnload: string;
    actionPending: string;
    stateLoaded: string;
    stateStarted: string;
    stateStopped: string;
    lifecycleActionError: (name: string, action: string) => string;
    lifecycleRefreshError: string;
  };

  // Live Preview
  livePreview: {
    title: string;
    desktop: string;
    tablet: string;
    mobile: string;
    refresh: string;
    openExternal: string;
    showCode: string;
    hideCode: string;
    loading: string;
    empty: string;
    emptyHint: string;
    showPanel: string;
    hidePanel: string;
    inspectElement: string;
    cancelInspect: string;
    inspectHint: string;
    aiEditTitle: string;
    aiEditCancel: string;
    aiEditPlaceholder: string;
    aiEditSend: string;
    aiEditQueued: string;
    aiEditUnavailable: string;
    officeEdit: string;
    officeSelect: string;
    officeCancelSelect: string;
    officeSelected: string;
    officeEditTitle: string;
    officeEditPlaceholder: string;
    officeEditHint: string;
    previewError: string;
    previewRetry: string;
    officeFidelity: string;
    humanEdit: string;
    humanEditing: string;
    humanUnsaved: string;
    humanSave: string;
    humanCancel: string;
    humanSaved: string;
    humanUndo: string;
    humanRestored: string;
    humanConflict: string;
    humanReloadLatest: string;
    humanUnavailable: string;
    humanDiscardTitle: string;
    humanDiscardDescription: string;
    humanDiscardConfirm: string;
  };

  // Code Status
  codeStatus: {
    processing: string;
    deploying: string;
    deployed: string;
    deployError: string;
  };

  // Selection Editor
  selectionEditor: {
    placeholder: string;
  };

  // Sidebar
  sidebar: {
    recentChats: string;
    newChat: string;
    chats: string;
    demoChats: string;
    sectionToday: string;
    sectionYesterday: string;
    sectionLast7Days: string;
    sectionLast30Days: string;
    sectionOlder: string;
    agents: string;
    skills: string;
    createTeam: string;
    switchAgent: string;
    selectAgent: string;
    confirmDeleteProject: (project: string) => string;
    confirmDeleteProjectTitle: string;
    confirmDeleteThread: (title: string) => string;
    tools: string;
    navigate: string;
    backgroundTasks: string;
    apiPublish: string;
    memory: string;
    taskBoard: string;
    workflows: string;
    evolution: string;
    observability: string;
    diagnostics: string;
    plugins: string;
    projects: string;
    channels: string;
    pairing: string;
    moveToProject: string;
    newProject: string;
    // Additional fields
    appAuth: string;
    agentSwarm: string;
    // Nav items (left primary + collapsible groups)
    navChat: string;
    navCode: string;
    navAdmin: string;
    navSwarm: string;
    navCompany: string;
    navTeam: string;
    navDatabase: string;
    navKnowledgeGraph: string;
    navReflex: string;
    navIntelligence: string;
    navAssistant: string;
    navPaperTrading: string;
    navPaperTradingDesc: string;
    navCommunity: string;
    navMcp: string;
    navEvolution: string;
    navProjects: string;
    navDesign: string;
    navNarrative: string;
    navPlugins: string;
    navHR: string;
    navComputer: string;
    navDesktopOrganizer: string;
    navArchitecture: string;
    groupTools: string;
    groupAdvancedTools: string;
    groupAdvanced: string;
    groupConnectors: string;
    groupOperations: string;
    groupSystem: string;
    // Agent / team footer
    noAgents: string;
    loadingAgents: string;
    agentsLoadFailed: string;
    retryAgents: string;
    remainingCredits: string;
    logout: string;
    noTeams: string;
    selectTeam: string;
    deleteTeam: string;
    newTeam: string;
    teamMembers: (n: number) => string;
    lockedAgentTooltip: (name: string) => string;
    adminAgentName: string;
    switchAgentLabel: string;
    switchAgentMenuTitle: string;
    openAgentHud: string;
    openAgentHudFor: (name: string) => string;
    currentAgent: string;
    soloChat: string;
    oneOnOneTask: string;
    soloTasks: string;
    groupTasks: string;
    recentThreadsSummary: (recent: number, hidden: number) => string;
    showMoreProjectThreads: (count: number) => string;
    showFewerProjectThreads: string;
    // Header/footer tooltips
    newChatTooltip: string;
    searchTooltip: string;
    settingsTooltip: string;
    // Project + chat list actions
    deleteProjectTooltip: string;
    deleteProjectFailed: string;
    deleteThreadTooltip: string;
    actionSort: string;
    actionNewProject: string;
    projectPickerFailed: string;
    actionNewTask: string;
    actionNewChat: string;
    actionNew: string;
    sectionStart: string;
    actionEnableProjectGrouping: string;
    actionDisableProjectGrouping: string;
    sectionProjects: string;
    sectionChats: string;
    sectionSessions: string;
    noChatsYet: string;
    collapseSidebar: string;
    expandSidebar: string;
    collapseSection: (label: string) => string;
    expandSection: (label: string) => string;
    projectNamePlaceholder: string;
    backToProjectList: string;
    openThreadFilesTooltip: string;
    // Surface switch + task statuses
    navBrowserSurface: string;
    // Pluggable module editor (DingTalk-style "edit sidebar" panel)
    editModules: string;
    editModulesDone: string;
    editModulesHint: string;
    modulePinned: string;
    moduleGroupWorkspace: string;
    moduleGroupKnowledge: string;
    moduleGroupCommunity: string;
    moduleGroupGrowth: string;
    sectionTaskHistory: string;
    noTaskHistory: string;
    unnamedTask: string;
    currentTaskSession: string;
    taskStatusRunning: string;
    taskStatusFailed: string;
    taskStatusPending: string;
    // Aria labels
    ariaCollapseLocalDatabase: string;
    ariaExpandLocalDatabase: string;
    ariaResizeSidebar: string;
    ariaResizeWorkbench: string;
    ariaChatWorkspace: string;
    ariaUtilityPanel: string;
    ariaAgentWorkbench: string;
    ariaToggleWorkbenchDrawer: string;
    // Storage library labels
    libraryApps: string;
    libraryDocs: string;
    libraryImages: string;
    libraryVideos: string;
    libraryComputer: string;
    libraryAuthorizedDirs: string;
    // Chats drawer
    searchChats: string;
    noMatchingChats: string;
  };

  // Floating REC recorder overlay
  recorder: {
    title: string;
    close: string;
    taskNameLabel: string;
    taskNamePlaceholder: string;
    stopFailed: string;
  };

  // Local brain readiness panel
  localBrain: {
    refresh: string;
    dismiss: string;
    title: string;
    ready: string;
    pending: (count: number) => string;
    checking: string;
    requestFailed: string;
    checkFailed: (error: string) => string;
    currentState: (detail: string) => string;
    nextStep: (action: string) => string;
  };

  // Team tasks panel
  teamTasksPanel: {
    emptyState: string;
    summary: (total: number, running: number, done: number) => string;
    newTask: string;
    loading: string;
    emptyFilter: string;
    autoMatch: string;
    artifactCount: (count: number) => string;
    rolesCompleted: (completed: number, total: number) => string;
    statusPending: string;
    timeline: {
      evidenceToggle: string;
      processCount: (count: number) => string;
      artifactCount: (count: number) => string;
      rawState: (included: boolean) => string;
      refreshing: string;
      empty: string;
    };
    toast: {
      runStarted: string;
      runFailed: string;
      taskPaused: string;
      pauseFailed: string;
      taskDeleted: string;
      deleteFailed: string;
    };
    deleteConfirmTitle: string;
    deleteConfirmDescription: (title: string) => string;
    events: {
      runStarted: string;
      roleStarted: (role?: string | null) => string;
      roleCompleted: (role?: string | null) => string;
      runDone: string;
      runFailed: string;
      runCancelled: string;
      fallback: (role?: string | null) => string;
    };
    roles: {
      planner: string;
      researcher: string;
      executor: string;
      critic: string;
      synthesizer: string;
      evaluator: string;
    };
  };

  // Browser settings page
  browserSettings: {
    title: string;
    description: string;
    tabBrowsers: string;
    tabExtension: string;
    tabCompare: string;
    systemEnv: string;
    browserList: string;
    refresh: string;
    noBrowsers: string;
    viaExtension: string;
    viaCdp: string;
    viaPlaywright: string;
    extensionInstalled: string;
    extensionNotInstalled: string;
    configureExtension: string;
    configureCdp: string;
    maxOpenTabs: string;
    maxOpenTabsDesc: string;
    maxSavedTabs: string;
    maxSavedTabsDesc: string;
    saveConfig: string;
    configSaved: string;
    installExtensionTitle: string;
    installExtensionDesc: string;
    step1Title: string;
    step1Desc: string;
    step1Action: string;
    step1Hint: string;
    step2Title: string;
    step2Desc: string;
    step2OpenFolder: string;
    step2CopyPath: string;
    step3Title: string;
    step3Desc: string;
    relayConnected: string;
    relayDisconnected: string;
    relayVersion: string;
    compareTitle: string;
    compareDesc: string;
    compareFeature: string;
    compareExtension: string;
    compareCdp: string;
    compareSetup: string;
    compareSetupExt: string;
    compareSetupCdp: string;
    compareReconnect: string;
    compareReconnectExt: string;
    compareReconnectCdp: string;
    compareChromeVersion: string;
    compareChromeVersionExt: string;
    compareChromeVersionCdp: string;
    compareStability: string;
    compareStabilityExt: string;
    compareStabilityCdp: string;
    compareInstall: string;
    compareInstallExt: string;
    compareInstallCdp: string;
    comparePrompt: string;
    comparePromptExt: string;
    comparePromptCdp: string;
    useExtension: string;
    useCdp: string;
    recommended: string;
    beta: string;
    high: string;
    medium: string;
    auto: string;
    manual: string;
    allVersions: string;
    // Page Agent integration
    pageAgentDesc: string;
    pageAgentDocs: string;
    pageAgentFeature1: string;
    pageAgentFeature1Desc: string;
    pageAgentFeature2: string;
    pageAgentFeature2Desc: string;
    pageAgentFeature3: string;
    pageAgentFeature3Desc: string;
    pageAgentFeature4: string;
    pageAgentFeature4Desc: string;
    pageAgentCmpArch: string;
    pageAgentCmpArchPA: string;
    pageAgentCmpArchRelay: string;
    pageAgentCmpControl: string;
    pageAgentCmpControlPA: string;
    pageAgentCmpControlRelay: string;
    pageAgentCmpProtocol: string;
    pageAgentCmpLogin: string;
    pageAgentCmpLoginPA: string;
    pageAgentCmpLoginRelay: string;
    pageAgentCmpVisual: string;
    pageAgentCmpVisualPA: string;
    pageAgentCmpVisualRelay: string;
    pageAgentEnabled: string;
    pageAgentDisabled: string;
    pageAgentEnabledDesc: string;
    pageAgentDisabledDesc: string;
    pageAgentEnable: string;
    pageAgentDisable: string;
    pageAgentEnableSuccess: string;
    // Browser page hardcoded Chinese
    systemArchitecture: string;
    step3DragHint: string;
    extProInstallOnce: string;
    extProNoManualAuth: string;
    extProAllVersions: string;
    extConNeedExtension: string;
    cdpProNoExtension: string;
    cdpProChrome144: string;
    cdpConReAuth: string;
    cdpConChrome144Only: string;
  };

  // MCP Connection Center
  mcpCenter: {
    integrations: string;
    connectionCenter: string;
    connectionCenterDesc: string;
    toolExtension: string;
    toolExtensionDesc: string;
    configGovernance: string;
    configGovernanceDesc: string;
  };

  // Intelligence Center
  intelligenceCenter: {
    researchOps: string;
    title: string;
    description: string;
    discoverChanges: string;
    // IntelligencePage extras
    pageSubtitle: string;
    pageBadge: string;
  };

  // Channels
  channels: {
    title: string;
    description: string;
    serviceRunning: string;
    serviceStopped: string;
    enabled: string;
    disabled: string;
    running: string;
    stopped: string;
    restart: string;
    restarting: string;
    restartSuccess: string;
    restartFailed: string;
    notConfigured: string;
    // New fields
    linked: string;
    notLinked: string;
    pairedUsers: string;
    pairedGroups: string;
    pendingRequests: string;
    noGroupPairing: string;
    configureAgent: string;
    configureAgentHint: string;
    agentConfigured: string;
    selectAgent: string;
    selectAgentTitle: (channel: string) => string;
    connectBot: string;
    bindWechat: string;
    howToConnect: string;
    connect: string;
    connecting: string;
    connectSuccess: string;
    connectFailed: string;
    cancel: string;
    clientId: string;
    clientSecret: string;
    appId: string;
    appSecret: string;
    botToken: string;
    applicationId: string;
    authorizeBot: string;
    wechatScanTitle: string;
    wechatScanHint: string;
    wechatWaiting: string;
    wechatConnectHint: string;
    channelCount: (n: number) => string;
    dingtalkName: string;
    wechatName: string;
    feishuName: string;
    wecomName: string;
    howToConnectLabel: string;
    connectDingtalk: string;
    connectFeishu: string;
    connectWechat: string;
    connectTelegram: string;
    dingtalkStep1: string;
    dingtalkStep2: string;
    dingtalkStep3: string;
    dingtalkStep4: string;
    dingtalkStep5: string;
    dingtalkClientIdPlaceholder: string;
    dingtalkClientSecretPlaceholder: string;
    feishuStep1: string;
    feishuStep2: string;
    feishuStep3: string;
    feishuStep4: string;
    feishuAppIdPlaceholder: string;
    feishuAppSecretPlaceholder: string;
    telegramStep1: string;
    telegramStep2: string;
    telegramStep3: string;
    telegramStep4: string;
    telegramBotTokenPlaceholder: string;
    wechatQrFailed: string;
    wechatConnectSuccess: string;
    wechatBot: string;
    channelBot: string;
    // ChannelsPage extras
    pageDescription: string;
    localDataNote: string;
    connectedCount: (n: number) => string;
    loading: string;
    loadFailed: string;
    retry: string;
    noRegistered: string;
    noRegisteredDescription: string;
    searchPlaceholder: string;
    filterAll: string;
    filterConnected: string;
    filterUnlinked: string;
    noSearchResults: string;
    noSearchResultsDescription: string;
    categoryOther: string;
    connectedBadge: string;
    toastAgentBound: string;
    toastBindFailed: string;
    toastAgentUnbound: string;
    toastUnbindFailed: string;
    assignDialogTitle: (name: string) => string;
    assignDialogDesc: string;
    noAgentsAvailable: string;
    unassignCurrent: string;
    unassignConfirmTitle: string;
    unassignConfirmDescription: string;
    howToSetup: string;
    clickToChangeAgent: string;
    handlingMessages: string;
    rebindOrUnbind: string;
    helpDocsComingSoon: string;
  };

  // Pairing Authorization
  pairing: {
    title: string;
    description: string;
    filterChannel: string;
    filterStatus: string;
    clearFilters: string;
    currentFilters: string;
    pendingSection: string;
    pendingNote: string;
    noPendingRequests: string;
    noPendingDesc: string;
    approve: string;
    reject: string;
    approveSuccess: string;
    rejectSuccess: string;
    statusPending: string;
    statusApproved: string;
    statusRejected: string;
    allChannels: string;
    allStatuses: string;
    user: string;
    group: string;
    requestedAt: string;
    expiresAt: string;
    operationFailed: string;
    notImplementedTitle: string;
    notImplementedDesc: string;
  };
  agentApi: {
    title: string;
    description: string;
    comingSoonTitle: string;
    comingSoonDesc: string;
    plannedEndpoint: string;
    sampleCalls: string;
    requestFormat: string;
  };

  // Paused tasks banner
  pausedTasksBanner: {
    paused: string;
    pendingPause: string;
    executing: string;
    continueBtn: string;
    clearBtn: string;
    pauseBtn: string;
    clearTitle: string;
    reasonUserRequest: string;
    reasonBudgetNearLimit: string;
    reasonIterationNearLimit: string;
    reasonExternal: string;
    resumedTitlePrefix: string;
    resumedDescWithThread: string;
    resumedTitleClearMark: string;
    resumedDescNoThread: string;
    clearedPrefix: string;
    pauseRequestedPrefix: string;
    pauseRequestedDesc: string;
    agentLabel: string;
    threadLabel: string;
    tokensLabel: string;
    costLabel: string;
    budgetDialogTitle: string;
    budgetDialogDesc: string;
    budgetResumedDesc: string;
    extraTokensKLabel: string;
    extraUsdLabel: string;
    extraIterationsLabel: string;
    notNowBtn: string;
    continueWithBudgetBtn: string;
  };
  armsDialog: {
    titlePrefix: string;
    description: string;
    wrenchTitle: string;
  };
  agentConfig: {
    dialogTitle: string;
    title: string;
    subtitle: string;
    configBadge: string;
    uidLabel: string;
    back: string;
    synced: string;
    unsaved: string;
    active: string;
    ready: string;
    loadoutReady: string;
    saved: string;
    saveFailed: (msg: string) => string;
    officialFaction: string;
    authorFaction: (author: string) => string;
    categoryRoles: Record<string, string>;
    categoryTypes: Record<string, string>;
    factionLabel: string;
    armCountLabel: string;
    armCount: (count: number) => string;
    skillCountLabel: string;
    skillCount: (count: number) => string;
    permissionLabel: string;
    guarded: string;
    permissionCount: (enabled: number, total: number) => string;
    visualTitle: string;
    visualSubtitle: string;
    visualStatus: string;
    visualWatermark: string;
    visualLoadoutLabel: string;
    visualSystemOnline: string;
    visualGenerateAction: string;
    visualGenerating: string;
    visualGenerateSuccess: string;
    visualGenerateFailed: (msg: string) => string;
    visualMissing: string;
    viewFront: string;
    viewSide: string;
    viewBack: string;
    basicTitle: string;
    basicSubtitle: string;
    descriptionLabel: string;
    modelLabel: string;
    modelHint: string;
    modelPlaceholder: string;
    defaultModel: string;
    armTitle: string;
    armSubtitle: string;
    noArms: string;
    advancedArmConfig: string;
    moreArms: (count: number) => string;
    emptyArmSlot: string;
    skillTitle: string;
    skillSubtitle: string;
    privateSkillsLabel: string;
    privateSkillsPlaceholder: string;
    characterFileLabel: string;
    characterBackgroundLabel: string;
    characterAgeLabel: string;
    characterTemperamentLabel: string;
    characterPersonalityLabel: string;
    characterBestForLabel: string;
    characterBoundaryLabel: string;
    characterVisualKeywordsLabel: string;
    characterProfileReady: string;
    characterPromptHint: string;
    characterSkillHiddenHint: string;
    capabilityPackLabel: string;
    characterBackground: (
      name: string,
      role: string,
      type: string,
      faction: string,
      description: string,
    ) => string;
    characterIntro: (
      name: string,
      role: string,
      type: string,
      faction: string,
      origin: string,
      personality: string,
      temperament: string,
    ) => string;
    characterDefaultOrigin: string;
    characterEpithets: Record<string, string>;
    characterQuotes: Record<string, string>;
    characterAgeArchetypes: Record<string, string>;
    characterPersonalities: Record<string, string>;
    characterTemperaments: Record<string, string>;
    characterVisualKeywords: Record<string, string[]>;
    keySkillsLabel: string;
    browseSkillWhitelist: string;
    availableSkillPoolLabel: string;
    availableSkillPoolCount: (selected: number, total: number) => string;
    availableSkillPoolHint: string;
    skillSlotHint: string;
    allSkillsWildcard: string;
    emptySkillSlot: string;
    moreSkills: (count: number) => string;
    extraAffinityLabel: string;
    extraAffinityPlaceholder: string;
    commaHint: string;
    promptTitle: string;
    promptSubtitle: string;
    soulPlaceholder: string;
    loadoutCheckTitle: string;
    loadoutCheckSubtitle: string;
    loadoutOk: string;
    checkNoArms: string;
    checkNoPrivateSkills: string;
    checkBlockedSkills: (count: number) => string;
    checkNoExecutableSkills: string;
    checkUnsavedChanges: string;
    configDockTitle: string;
    configureProfileAction: string;
    configureProfileHint: string;
    configureArmAction: string;
    configureArmHint: string;
    configureSkillsAction: string;
    configureSkillsHint: string;
    configurePermissionsAction: string;
    configurePermissionsHint: string;
    routingConfig: string;
    saveTitle: string;
    saveSubtitle: string;
    saveButton: string;
    savedButton: string;
    resetButton: string;
    noChange: string;
    modified: string;
    signedDelta: (count: number) => string;
  };

  // Agent role profile dialog
  agentRoleProfile: {
    imageReadFailed: string;
    switchToAgent: (name: string) => string;
    generateVisualPromptTitle: string;
    generateVisualPromptDescription: string;
    visualPromptGroupStyle: string;
    visualPromptGroupComposition: string;
    visualPromptGroupBackground: string;
    visualPromptGroupQuality: string;
    visualPromptOptionGameCharacter: string;
    visualPromptOptionCleanAnime: string;
    visualPromptOptionSemiReal: string;
    visualPromptOptionFullBody: string;
    visualPromptOptionSafeHeadroom: string;
    visualPromptOptionAvatarReady: string;
    visualPromptOptionThreeViewConsistency: string;
    visualPromptOptionTransparent: string;
    visualPromptOptionSoftShadow: string;
    visualPromptOptionHighResolution: string;
    visualPromptOptionNoArtifacts: string;
    customAdditions: string;
    customPromptPlaceholder: string;
    referenceImages: string;
    referenceImagesHint: (count: number) => string;
    referenceImageUrlPlaceholder: string;
    upload: string;
    referenceImageAlt: (index: number) => string;
    removeReferenceImage: string;
    referenceImagesGenerateHint: (count: number) => string;
    reset: string;
    cancel: string;
    generateThreeViews: string;
    codeMode: string;
    codeModeDescription: string;
    toggleCodeMode: string;
    saveCodeMode: string;
    coderBestFor: string[];
    coderBoundaries: string[];
    researcherBestFor: string[];
    researcherBoundaries: string[];
    growthBestFor: string[];
    growthBoundaries: string[];
    ecommerceBestFor: string[];
    ecommerceBoundaries: string[];
    aoiBestFor: string[];
    aoiBoundaries: string[];
    defaultBestFor: string[];
    defaultBoundaries: string[];
  };

  chatPage: {
    stopNote: string;
  };

  // Agents
  agents: {
    title: string;
    description: string;
    newAgent: string;
    emptyTitle: string;
    emptyDescription: string;
    chat: string;
    delete: string;
    deleteConfirm: string;
    deleteSuccess: string;
    newChat: string;
    createPageTitle: string;
    createPageSubtitle: string;
    nameStepTitle: string;
    nameStepHint: string;
    nameStepPlaceholder: string;
    nameStepContinue: string;
    nameStepInvalidError: string;
    nameStepAlreadyExistsError: string;
    nameStepNetworkError: string;
    nameStepCheckError: string;
    nameStepBootstrapMessage: string;
    save: string;
    saving: string;
    saveRequested: string;
    saveHint: string;
    saveCommandMessage: string;
    agentCreatedPendingRefresh: string;
    more: string;
    agentCreated: string;
    startChatting: string;
    backToGallery: string;
    agentNew: {
      pageTitle: string;
      pageSubtitle: string;
      placeholder: string;
      buttons: {
        checking: string;
        generate: string;
        back: string;
        autoConfig: string;
        generateConfig: string;
      };
      labels: {
        permissions: string;
      };
      roles: {
        id: string;
        label: string;
        nameSuggestion: string;
        brief: string;
      }[];
      scenarios: {
        id: string;
        label: string;
        brief: string;
        permissions: string[];
      }[];
      abilities: {
        id: string;
        label: string;
        arms: string[];
        skills: string[];
        brief: string;
      }[];
      templates: {
        id: string;
        name: string;
        nameSuggestion: string;
        description: string;
        integrations: string[];
        capabilities: string[];
      }[];
    };
  };

  // Agent card
  agentCard: {
    chat: string;
    chatAriaLabel: (name: string) => string;
    addOnDemand: string;
    addOnDemandAriaLabel: (name: string) => string;
    profile: string;
    profileAriaLabel: (name: string) => string;
    deleteAriaLabel: (name: string) => string;
    deleteTitle: (name: string) => string;
    deleteConfirm: (name: string) => string;
  };

  // Enterprise assets tab
  enterpriseAssetsTab: {
    loading: string;
    notAvailableTitle: string;
    notAvailableHintPrefix: string;
    notAvailableHintSuffix: string;
    empty: string;
    header: (count: number) => string;
    install: string;
    installing: string;
    importSuccess: (name: string) => string;
    importFailed: (msg: string) => string;
  };

  // Breadcrumb
  breadcrumb: {
    workspace: string;
    chats: string;
  };

  // Workspace
  workspace: {
    officialWebsite: string;
    githubTooltip: string;
    settingsAndMore: string;
    visitGithub: string;
    reportIssue: string;
    contactUs: string;
    about: string;
    modes: {
      chat: string;
      team: string;
      code: string;
    };
    // Landing page
    landing: {
      tagline: string;
      badge: string;
      headline: string;
      description: string;
      newTask: string;
      codeTask: string;
      systemLoop: {
        goal: string;
        plan: string;
        execute: string;
        observe: string;
        remember: string;
        improve: string;
      };
      primaryRoutes: {
        agentTask: {
          title: string;
          description: string;
        };
        codeWork: {
          title: string;
          description: string;
        };
        inspectRuntime: {
          title: string;
          description: string;
        };
      };
      conceptMap: {
        title: string;
        planner: string;
        plannerAlias: string;
        plannerValue: string;
        reflex: string;
        reflexAlias: string;
        reflexValue: string;
        safety: string;
        safetyAlias: string;
        safetyValue: string;
        workflow: string;
        workflowAlias: string;
        workflowValue: string;
      };
      operatingSurfaces: {
        title: string;
        openTraces: string;
        skills: {
          title: string;
          text: string;
        };
        memory: {
          title: string;
          text: string;
        };
        reflex: {
          title: string;
          text: string;
        };
        automation: {
          title: string;
          text: string;
        };
      };
    };
  };

  // Conversation
  conversation: {
    noMessages: string;
    startConversation: string;
    noArtifactSelected: string;
    selectArtifactToView: string;
    artifactsTitle: string;
    artifactsTabChanges: string;
    artifactsTabPreview: string;
    noChangesArtifacts: string;
    noPreviewArtifacts: string;
    retry: string;
    messageQueued: string;
    messageSending: string;
    messageSendFailed: string;
    previousMessagePending: string;
    steeringTurnUnavailable: string;
    editResend: string;
    regenerateResponse: string;
    forkFromHere: string;
    forkedThread: string;
    forkFailed: string;
    goodResponse: string;
    badResponse: string;
    feedbackThanks: string;
    feedbackRecorded: string;
    feedbackFailed: string;
    interruptedMessage: string;
    pausedMessage: string;
    cancelledMessage: string;
    strategyReflex: string;
    strategyReact: string;
    strategyDirectLlm: string;
    strategyVote: string;
    strategyCache: string;
    clarificationChoose: string;
    clarificationAutoSubmit: (seconds: number) => string;
    clarificationOtherPlaceholder: string;
  };

  // Chats
  chats: {
    searchChats: string;
    workspace: string;
    description: string;
    newChat: string;
    noChats: string;
    noChatsDescription: string;
    startNow: string;
    noMatch: string;
    noMatchDescription: string;
  };

  // Page titles (document title)
  pages: {
    appName: string;
    chats: string;
    newChat: string;
    untitled: string;
  };

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => string;
    lessSteps: string;
    executeCommand: string;
    presentFiles: string;
    needYourHelp: string;
    useTool: (toolName: string) => string;
    searchForRelatedInfo: string;
    searchForRelatedImages: string;
    searchFor: (query: string) => string;
    searchForRelatedImagesFor: (query: string) => string;
    searchOnWebFor: (query: string) => string;
    viewWebPage: string;
    listFolder: string;
    readFile: string;
    writeFile: string;
    clickToViewContent: string;
    writeTodos: string;
    skillInstallTooltip: string;
    toastSkillInstallFailed: string;
    toastExportConversationFailed: string;
  };

  // Uploads
  uploads: {
    uploading: string;
    uploadingFiles: string;
    uploadProgress: (percent: number) => string;
    uploadFailed: string;
    retryUpload: string;
    waitingForUpload: string;
  };

  // Streaming status
  streaming: {
    thinking: string;
    thoughtProcess: string;
    connectionLost: string;
    networkLost: string;
    turnFailed: string;
    guardBlocked: string;
    lifecycleFailed: string;
    workspaceWriteRequired: string;
    verificationRequired: string;
    environmentBlocked: string;
    environmentBlockedAuthorizeCommon: string;
    environmentBlockedAuthorizeFull: string;
    blockedOnUser: string;
    streamEndpointUnavailable: string;
    iteration: (count: number) => string;
    toolCalls: (count: number) => string;
    generating: string;
    codeBlockWrap: string;
    codeBlockScroll: string;
  };

  subagents: {
    subagent: string;
    executing: (count: number) => string;
    parallelExecution: string;
    pending: string;
    reasoning: string;
    iterating: string;
    generating: string;
    analyzing: string;
    summarizing: string;
    in_progress: string;
    completed: string;
    failed: string;
    cancelled: string;
    timed_out: string;
    running: string;
    expandAll: string;
    collapseAll: string;
    iterations: string;
    duration: string;
    filesModified: string;
    executionHistory: string;
    modifiedFiles: string;
    viewDetails: string;
  };

  todoList: {
    title: string;
  };

  executionPanel: {
    readFile: string;
    searchFiles: string;
    searchContent: string;
    listDir: string;
    gitDiff: string;
    gitStatus: string;
    diagnostics: string;
    writeFile: string;
    editFile: string;
    createFile: string;
    gitCommit: string;
    editNotebook: string;
    terminal: string;
    execute: string;
    subAgent: string;
    assignMember: string;
    run: string;
    search: string;
    browse: string;
    fetch: string;
  };

  // Token Usage
  tokenUsage: {
    title: string;
    input: string;
    output: string;
    total: string;
    speed: string;
    context: string;
  };

  // Model Router
  modelRouter: {
    title: string;
    auto: string;
    manual: string;
    taskType: string;
    selectedModel: string;
    originalModel: string;
    score: string;
    modelScores: string;
    recentRouting: string;
    loadingHistory: string;
    coding: string;
    reasoning: string;
    creative: string;
    simple: string;
    math: string;
  };

  // Shortcuts
  shortcuts: {
    searchActions: string;
    noResults: string;
    actions: string;
    keyboardShortcuts: string;
    keyboardShortcutsDescription: string;
    openCommandPalette: string;
    commandPaletteDescription: string;
    toggleSidebar: string;
    focusChatInput: string;
    toggleRightPanel: string;
    pages: string;
  };

  // Slash Commands
  slashCommands: {
    clear: string;
    compact: string;
    mode: string;
    model: string;
    settings: string;
    commit: string;
    review: string;
    test: string;
    fix: string;
    search: string;
    memory: string;
    cost: string;
    context: string;
    init: string;
    wiki: string;
    monitor: string;
    personality: string;
    personalityApplied: string;
    arena: string;
    quest: string;
    record: string;
    replay: string;
    skills: string;
    deploy: string;
  };

  // Tool Approval
  toolApproval: {
    requiresApproval: string;
    approve: string;
    reject: string;
    approved: string;
    rejected: string;
    tools: {
      bash: string;
      write_file: string;
      str_replace: string;
      git_commit: string;
      schedule_cron: string;
      remote_trigger: string;
    };
  };

  // Diff Editor
  diffEditor: {
    title: string;
    changedFiles: string;
    noChanges: string;
    acceptAll: string;
    rejectAll: string;
    accept: string;
    reject: string;
    accepted: string;
    rejected: string;
    pending: string;
    additions: string;
    deletions: string;
    sideBySide: string;
    unified: string;
    expandLines: string;
    collapseLines: string;
    searchFiles: string;
    sortByName: string;
    sortByStatus: string;
    sortByChanges: string;
    fileAdded: string;
    fileModified: string;
    fileDeleted: string;
    hunkAccept: string;
    hunkReject: string;
    allAccepted: string;
    allRejected: string;
    filesChanged: string;
  };

  // Settings
  settings: {
    title: string;
    description: string;
    sections: {
      account: string;
      subscription: string;
      appearance: string;
      general: string;
      conversation: string;
      memory: string;
      tools: string;
      skills: string;
      notification: string;
      browser: string;
      observability: string;
      privacy: string;
      about: string;
      automation: string;
      evolution: string;
      sandbox: string;
    };
    automation: {
      title: string;
      description: string;
      restartRequiredTitle: string;
      restartRequiredBody: string;
      browserTitle: string;
      browserDesc: string;
      desktopTitle: string;
      desktopDesc: string;
      localToolsTitle: string;
      localToolsDesc: string;
      groupLabel: string;
      reset: string;
      save: string;
      saveSuccess: string;
      saveFailed: string;
      saveDescription: string;
      nextStepSaveTitle: string;
      nextStepVerifyTitle: string;
      nextStepSaveHint: string;
      nextStepVerifyHint: string;
      nextStepDisabledTitle: string;
      nextStepDisabledHint: string;
      openComputerTool: string;
      loading: string;
      loadFailed: string;
      restartConfirmTitle: string;
      restartConfirmBody: string;
      restartLater: string;
      restartNow: string;
      restarting: string;
      restartFailed: string;
      restartManualOnly: string;
      rules: {
        sectionTitle: string;
        sectionDescription: string;
        emptyState: string;
        loading: string;
        loadFailed: string;
        addTitle: string;
        effectLabel: string;
        effectAllow: string;
        effectDeny: string;
        toolLabel: string;
        toolPlaceholder: string;
        argsLabel: string;
        argsPlaceholder: string;
        reasonLabel: string;
        reasonPlaceholder: string;
        addButton: string;
        adding: string;
        addError: string;
        deleteButton: string;
        deleteConfirmTitle: string;
        deleteConfirmHint: string;
        deleteError: string;
        moveUpButton: string;
        moveDownButton: string;
        moveError: string;
        firstMatchHint: string;
      };
    };
    evolution: {
      title: string;
      description: string;
      refresh: string;
      loading: string;
      loadFailed: string;
      schedulerStatus: string;
      runningLabel: string;
      runningYes: string;
      runningNo: string;
      intervalLabel: string;
      tickedLabel: string;
      tickedUnit: string;
      learnedRulesTitle: string;
      scanned: string;
      trajectoryUnit: string;
      failed: string;
      clusters: string;
      produced: string;
      ruleUnit: string;
      lastTick: string;
      noFailureData: string;
      ruleTrigger: string;
      ruleMitigation: string;
      recipeScoreTitle: string;
      noData: string;
      colRecipe: string;
      colUses: string;
      colSuccessRate: string;
      colAvgSteps: string;
      colVerdict: string;
      colScore: string;
      gepaTitle: string;
      gepaScannedPrefix: string;
      gepaManifestUnit: string;
      gepaSkippedPrefix: string;
      gepaDryRunBadge: string;
      gepaAutoApplyBadge: string;
      gepaDryRunHint: string;
      notGenerated: string;
      camouflageTitle: string;
      camouflageDescription: string;
      camouflageDisabledHint: string;
      camouflageEnabledBadge: string;
      camouflageDisabledBadge: string;
      camouflageVariantsLabel: string;
      camouflageStepsLabel: string;
      camouflageRetiredLabel: string;
      camouflageBoostedLabel: string;
      camouflageLastStepLabel: string;
      camouflageNoVariants: string;
      camouflageOriginSeed: string;
      camouflageOriginMutation: string;
      camouflageOriginCrossover: string;
    };
    memory: {
      title: string;
      description: string;
      empty: string;
      rawJson: string;
      exportButton: string;
      exportSuccess: string;
      importButton: string;
      importConfirmTitle: string;
      importConfirmDescription: string;
      importFileLabel: string;
      importInvalidFile: string;
      importFileTooLarge: string;
      importSuccess: string;
      manualFactSource: string;
      addFact: string;
      addFactTitle: string;
      editFactTitle: string;
      addFactSuccess: string;
      editFactSuccess: string;
      clearAll: string;
      clearAllConfirmTitle: string;
      clearAllConfirmDescription: string;
      clearAllSuccess: string;
      factDeleteConfirmTitle: string;
      factDeleteConfirmDescription: string;
      factDeleteSuccess: string;
      factContentLabel: string;
      factCategoryLabel: string;
      factConfidenceLabel: string;
      factContentPlaceholder: string;
      factCategoryPlaceholder: string;
      factConfidenceHint: string;
      factSave: string;
      factEditorDescription: string;
      factValidationContent: string;
      factValidationConfidence: string;
      noFacts: string;
      summaryReadOnly: string;
      memoryFullyEmpty: string;
      factPreviewLabel: string;
      searchPlaceholder: string;
      filterLabel: string;
      filterAll: string;
      filterFacts: string;
      filterSummaries: string;
      noMatches: string;
      projectScope: string;
      agentScope: string;
      globalScope: string;
      saved: string;
      actionFailed: string;
      configLoading: string;
      configLoadFailed: string;
      enableMemory: string;
      enableMemoryDesc: string;
      autoCapture: string;
      autoCaptureDesc: string;
      injectOnReply: string;
      injectOnReplyDesc: string;
      scopeLabel: string;
      markdown: {
        overview: string;
        userContext: string;
        work: string;
        personal: string;
        topOfMind: string;
        historyBackground: string;
        recentMonths: string;
        earlierContext: string;
        longTermBackground: string;
        updatedAt: string;
        facts: string;
        empty: string;
        table: {
          category: string;
          confidence: string;
          confidenceLevel: {
            veryHigh: string;
            high: string;
            normal: string;
            unknown: string;
          };
          content: string;
          source: string;
          createdAt: string;
          view: string;
        };
      };
    };
    appearance: {
      themeTitle: string;
      themeDescription: string;
      system: string;
      light: string;
      dark: string;
      systemDescription: string;
      lightDescription: string;
      darkDescription: string;
      paletteTitle: string;
      paletteDescription: string;
      paletteRose: string;
      paletteRoseDescription: string;
      paletteSteel: string;
      paletteSteelDescription: string;
      paletteEmerald: string;
      paletteEmeraldDescription: string;
      paletteViolet: string;
      paletteVioletDescription: string;
      paletteAmber: string;
      paletteAmberDescription: string;
      paletteTeal: string;
      paletteTealDescription: string;
      paletteApricot: string;
      paletteApricotDescription: string;
      paletteMint: string;
      paletteMintDescription: string;
      paletteGroupSoft: string;
      paletteGroupDeep: string;
      paletteCustom: string;
      paletteCustomDescription: string;
      paletteCustomHint: string;
      languageTitle: string;
      languageDescription: string;
      languageEnglish: string;
      languageChineseSimplified: string;
      chatFontSizeTitle: string;
      chatFontSizeDescription: string;
      chatFontSizeSmall: string;
      chatFontSizeMedium: string;
      chatFontSizeLarge: string;
      conversationDetailLevelTitle: string;
      conversationDetailLevelDescription: string;
      conversationDetailLevelLow: string;
      conversationDetailLevelMedium: string;
      conversationDetailLevelHigh: string;
      cornerRadiusTitle: string;
      cornerRadiusDescription: string;
      cornerCrisp: string;
      cornerSoft: string;
      cornerDefault: string;
      cornerRound: string;
      cornerPill: string;
      uiDensityTitle: string;
      uiDensityDescription: string;
      densityRelaxed: string;
      densityComfortable: string;
      densityCompact: string;
      densityDense: string;
      densityUltraDense: string;
    };
    tools: {
      title: string;
      description: string;
    };
    skills: {
      title: string;
      description: string;
      createSkill: string;
      emptyTitle: string;
      emptyDescription: string;
      emptyButton: string;
      enabledDescription: string;
      noMatch: (query: string) => string;
      searchPlaceholder: string;
      tabMarket: string;
      tabInstalled: string;
      tabPerformance: string;
      capabilities: string;
      skillsCenter: string;
      skillsCenterDesc: string;
      skillMarket: string;
      skillMarketDesc: string;
      runPerformance: string;
      runPerformanceDesc: string;
      discoverFromMarket: string;
    };
    notification: {
      title: string;
      description: string;
      enableNotification: string;
      permissionGranted: string;
      permissionPrompt: string;
      permissionDenied: string;
      requestPermission: string;
      deniedHint: string;
      testButton: string;
      testTitle: string;
      testBody: string;
      testSent: string;
      requestFailed: string;
      notSupported: string;
      disableNotification: string;
    };
    acknowledge: {
      emptyTitle: string;
      emptyDescription: string;
    };
    account: {
      title: string;
      description: string;
      profile: {
        title: string;
        description: string;
        displayName: string;
        displayNamePlaceholder: string;
        bio: string;
        bioPlaceholder: string;
        avatar: string;
        changeAvatar: string;
        removeAvatar: string;
        saveChanges: string;
        saving: string;
        saved: string;
      };
      linkedAccounts: {
        title: string;
        description: string;
        connect: string;
        disconnect: string;
        connected: string;
        notConnected: string;
      };
      privacy: {
        title: string;
        description: string;
        shareUsageData: string;
        shareUsageDataDescription: string;
        allowAnalytics: string;
        allowAnalyticsDescription: string;
      };
      dangerZone: {
        title: string;
        description: string;
        deleteAccount: string;
        deleteAccountDescription: string;
        deleteAccountConfirm: string;
      };
    };
    subscription: {
      title: string;
      description: string;
      currentPlan: string;
      freeTierDesc: string;
      paidTierDesc: string;
      upgrade: string;
      downgrade: string;
      billingHistory: string;
      paymentMethod: string;
      cancelSubscription: string;
      planTitle: string;
      planDescription: string;
      free: string;
      autoRenewal: string;
      expiresOn: (date: string) => string;
      perMonth: string;
      bonus: string;
      cancel: string;
      usageTitle: string;
      usageDescription: (start: string, end: string) => string;
      apiRequests: string;
      requestsRemaining: (n: string, percent: string) => string;
      tokens: string;
      currentPeriodCost: string;
      recentUsage: string;
      recentUsageDescription: string;
      colTime: string;
      colType: string;
      colModel: string;
      colSource: string;
      colCost: string;
      billingHistoryTitle: string;
      billingHistoryDescription: string;
      colDescription: string;
      colDate: string;
      colStatus: string;
      colAmount: string;
      availablePlans: string;
      availablePlansDescription: string;
      currentBadge: string;
      requestsPerMonth: (n: string) => string;
      agentsCount: (n: number) => string;
      workflowsCount: (n: number) => string;
      prioritySupport: string;
      currentPlanBtn: string;
      selectFree: string;
      subscribe: string;
    };
    model: {
      title: string;
      customModels: string;
      addCustomModel: string;
      emptyCustomModels: string;
      externalModelRisk: string;
      provider: string;
      modelIdPlaceholder: string;
      displayName: string;
      displayNamePlaceholder: string;
      providerLabel: string;
      providerAutoHint: string;
      apiKey: string;
      apiKeyPlaceholder: string;
      getApiKey: string;
      fillModelId: string;
      apiProtocol: string;
      baseUrlLabel: string;
      baseUrlPlaceholder: string;
      extraHeadersTitle: string;
      extraHeadersPlaceholder: string;
      extraHeadersHint: string;
      requiredFields: string;
      fillRequiredBeforeTest: string;
      testEndpointHint: string;
      updateFailed: string;
      networkError: string;
      editModelTitle: (name: string) => string;
      keepApiKeyHint: string;
      saveSuccess: string;
      notTested: string;
      testFailed: string;
      saveRequiresTestPass: string;
      testConnection: string;
      showApiKey: string;
      hideApiKey: string;
      diagnoseHealthy: string;
      diagnoseIssues: (issues: string) => string;
      deleteConfirm: (name: string) => string;
      deleteModelTitle: string;
      gatewayReturned: (status: number) => string;
      cannotReachGateway: string;
      gatewayUrl: string;
      connected: string;
      disconnected: string;
      reconnect: string;
      diagnose: string;
      port: string;
      thinkingLabel: string;
      defaultReasoningEffortLabel: string;
      defaultReasoningEffortFollow: string;
      defaultReasoningEffortOff: string;
      defaultReasoningEffortHigh: string;
      defaultReasoningEffortMax: string;
      defaultReasoningEffortNone: string;
      visionLabel: string;
      visionDetected: string;
      visionNotSupported: string;
      millionContextLabel: string;
      backendUrlHint: string;
      connectionHelp: string;
      connectionHelpReconnect: string;
      setDefaultHint: string;
      connectionHelpDiagnose: string;
      loadFailed: string;
      setDefaultSuccess: string;
      setDefaultFailed: string;
      deleteSuccess: string;
      deleteFailed: string;
      systemDefault: string;
      setAsDefault: string;
      noOfficialModels: string;
      gatewayHosted: string;
      accountNotLinked: string;
      gatewayNotEnabled: string;
      officialModels: string;
      officialModelsHint: string;
      modelCount: (count: number) => string;
      modelList: {
        label: string;
        hint: string;
        pickerDefault: string;
        performanceTier: string;
        pickerDefaultAndPerformance: string;
        fallback: string;
        addButton: string;
        removeTooltip: string;
        empty: string;
      };
      compatDiagnostics: {
        title: string;
        loading: string;
        unavailable: string;
        notApplicable: string;
        fallbacks: (count: number) => string;
        headers: (names: string) => string;
        removedFields: (fields: string, count: number) => string;
        changedFields: (fields: string, count: number) => string;
        addedFields: (fields: string, count: number) => string;
        compatScore: (score: string) => string;
        normalizationHints: (hints: string, count: number) => string;
        compatibilityNotes: (notes: string, count: number) => string;
        retryReasons: (reasons: string, count: number) => string;
        loadFailed: string;
      };
      localModels: {
        title: string;
        subtitle: string;
        scanButton: string;
        scanButtonScanning: string;
        empty: string;
        providerHint: string;
        modelsCount: (n: number) => string;
        emptyHint: string;
        importButton: string;
        importingButton: string;
        imported: string;
        importFailed: string;
        serviceStatus: {
          ok: string;
          empty: string;
          error: string;
        };
      };
      providers: {
        zhipu: string;
        aliyun: string;
        tencent: string;
        volcengine: string;
      };
    };
    echoMix: {
      title: string;
      description: string;
      proposersLabel: string;
      noCandidates: string;
      aggregatorLabel: string;
      aggregatorDefault: string;
      nLabel: string;
      saveButton: string;
      saveSuccess: string;
      saveFailed: (status: number) => string;
      saveFailedFallback: string;
    };
    dialog: {
      dragToResize: string;
      searchPlaceholder: string;
      clearSearch: string;
      quickAccess: string;
      sectionsLabel: string;
      resultsCount: (count: number) => string;
      noSearchResultsTitle: string;
      noSearchResultsDescription: string;
      sectionKeywords: {
        account: string[];
        subscription: string[];
        appearance: string[];
        models: string[];
        notification: string[];
        memory: string[];
        automation: string[];
        mcp: string[];
        privacy: string[];
        observability: string[];
        about: string[];
        sandbox: string[];
      };
    };
  };

  // Sandbox / execution permission settings
  sandboxSettings: {
    title: string;
    description: string;
    activeTag: string;
    scopeNote: string;
    restartHint: string;
    envTitle: string;
    envDesc: string;
    permissionTitle: string;
    permissionDesc: string;
    guardianTitle: string;
    guardianDesc: string;
    guardianToggleLabel: string;
    guardianToggleDesc: string;
    guardianModelLabel: string;
    guardianModelHint: string;
    toastGuardianOn: string;
    toastGuardianOff: string;
    toastEnvSwitched: (label: string) => string;
    toastPermissionSwitched: (label: string) => string;
    toastFailed: (msg: string) => string;
    env: {
      sandbox: {
        label: string;
        description: string;
      };
      local: {
        label: string;
        description: string;
      };
    };
    permission: {
      default: {
        label: string;
        description: string;
      };
      acceptEdits: {
        label: string;
        description: string;
      };
      bypassPermissions: {
        label: string;
        description: string;
      };
    };
    networkTitle: string;
    networkDesc: string;
    presetDomainsNote: string;
    toastNetworkSwitched: (label: string) => string;
    network: {
      deny: {
        label: string;
        description: string;
      };
      common: {
        label: string;
        description: string;
      };
      full: {
        label: string;
        description: string;
      };
    };
    replyStyleTitle: string;
    replyStyleDesc: string;
    toastReplyStyleSwitched: (label: string) => string;
    replyStyle: {
      default: {
        label: string;
        description: string;
      };
      professional: {
        label: string;
        description: string;
      };
      friendly: {
        label: string;
        description: string;
      };
      concise: {
        label: string;
        description: string;
      };
      socratic: {
        label: string;
        description: string;
      };
    };
  };

  // Task Board
  taskBoard: {
    title: string;
    description: string;
    refresh: string;
    retry: string;
    loadFailed: string;
    noTasks: string;
    noTasksDesc: string;
    kanban: string;
    timeline: string;
    list: string;
    schedules: string;
    queued: string;
    running: string;
    completed: string;
    failed: string;
    all: string;
    background: string;
    quest: string;
    scheduled: string;
    type: string;
    name: string;
    status: string;
    duration: string;
    created: string;
    noTasksDescription: string;
    // Task card extras
    paused: string;
    cancelled: string;
    progress: string;
    phase: string;
    task: string;
    intelligence: string;
    // Stats bar
    totalTasks: string;
    successRate: string;
    avgDuration: string;
    types: string;
    across: (count: number) => string;
    operations: string;
    unifiedView: string;
    noTimelineTasks: string;
    inProgress: string;
    zoomOut: string;
    zoomIn: string;
    justNow: string;
    minutesAgo: string;
    hoursAgo: string;
    daysAgo: string;
    filterByType: string;
    taskDetails: (name: string) => string;
    timelineChart: string;
    zoomReset: (percent: number) => string;
  };

  // Arena
  arena: {
    title: string;
    blindAB: string;
    battle: string;
    leaderboard: string;
    battleTitle: string;
    battleDesc: string;
    promptPlaceholder: string;
    modelA: string;
    modelB: string;
    aIsBetter: string;
    bIsBetter: string;
    tie: string;
    bothBad: string;
    eloLeaderboard: string;
    model: string;
    elo: string;
    winRate: string;
    battles: string;
    battleInProgress: string;
    sendingPrompt: string;
    prompt: string;
    whichBetter: string;
    choseA: string;
    choseB: string;
    votedTie: string;
    votedBothBad: string;
    voted: string;
    identitiesRevealed: string;
    newBattle: string;
    noBattles: string;
    wins: string;
    losses: string;
    ties: string;
  };

  // Quest
  questMode: {
    analyze: string;
    plan: string;
    execute: string;
    verify: string;
    report: string;
    reject: string;
    approveExecute: string;
    startQuest: string;
    questDesc: string;
    requirementPlaceholder: string;
    executionPlan: string;
    verificationPassed: string;
    verificationIssues: string;
    tests: string;
    lintErrors: string;
    typeErrors: string;
    requirementChecks: string;
    issues: string;
    questCompleted: string;
    questFailed: string;
    stepsCompleted: string;
    filesChanged: string;
    changedFiles: string;
    remainingTodos: string;
    startDescription: string;
    title: string;
    active: string;
    analyzing: string;
    generatingPlan: string;
    rejectConfirmTitle: string;
    rejectConfirmDescription: string;
    cancelConfirmTitle: string;
    cancelConfirmDescription: string;
    cancelConfirmLabel: string;
    verifyingResults: string;
    generatingReport: string;
    newQuest: string;
    cancelled: string;
    startNewQuest: string;
    quest: string;
    startFailed: string;
    approveFailed: string;
    rejectFailed: string;
    cancelFailed: string;
  };

  // Knowledge Graph
  knowledgeGraph: {
    title: string;
    graph: string;
    communities: string;
    query: string;
    searchPlaceholder: string;
    noEntities: string;
    noEntitiesDesc: string;
    queryPlaceholder: string;
    deleteEntity: string;
    searchEntities: string;
    entitiesExtracted: string;
    detect: string;
    noCommunities: string;
    clickDetect: string;
    askQuestion: string;
    askToSearch: string;
    hybridDescription: string;
    // KnowledgeGraphPanel extras
    emptyStateTitle: string;
    emptyStateHint: string;
    totalEntities: string;
    totalRelationships: string;
    entityTypesCount: string;
    relationshipsHeader: string;
    loadFailed: string;
    pageSubtitle: string;
    startTask: string;
    refresh: string;
    foundEntities: (found: number, total: number) => string;
    totalEntitiesCount: (total: number) => string;
    clearSearch: string;
    noMatchingEntities: string;
    noMatchingEntitiesHint: string;
  };

  // Background Tasks
  backgroundTasks: {
    title: string;
    queued: string;
    running: string;
    paused: string;
    completed: string;
    failed: string;
    cancelled: string;
    interrupted: string;
    pause: string;
    resume: string;
    cancel: string;
    delete: string;
    back: string;
    refresh: string;
    newTask: string;
    taskPlaceholder: string;
    waitingOutput: string;
    noOutput: string;
    taskName: string;
    taskNamePlaceholder: string;
    promptLabel: string;
    promptPlaceholder: string;
    runInBackground: string;
    noTasks: string;
    noTasksDescription: string;
    loading: string;
    loadFailed: string;
    retry: string;
    activeCount: (count: number) => string;
    agentLabel: string;
    threadLabel: string;
    durationLabel: string;
    taskFinished: (status: string) => string;
    justNow: string;
    minutesAgo: (count: number) => string;
    hoursAgo: (count: number) => string;
    daysAgo: (count: number) => string;
    unnamedTask: string;
    cancelConfirm: (name: string) => string;
    deleteConfirm: (name: string) => string;
  };

  unifiedStore: {
    title: string;
    subtitle: string;
    systemStatus: string;
    tabs: {
      agents: string;
      apps: string;
      plugins: string;
      skills: string;
      registry: string;
    };
    browserPlugins: {
      title: string;
      electronBadge: string;
      webPreviewBadge: string;
      descElectron: string;
      descWeb: string;
      installLocal: string;
      refreshAria: string;
      listFailed: string;
      installFailed: string;
      statusFailed: string;
      removeConfirm: string;
      removeFailed: string;
      enabled: string;
      disabled: string;
      removeAria: (name: string) => string;
      emptyElectron: string;
      emptyElectronHint: string;
      emptyWeb: string;
      placeholderTitle: string;
      placeholderDesc: string;
      placeholderBrowserTitle: string;
      placeholderBrowserDesc: string;
      placeholderSkillTitle: string;
      placeholderSkillDesc: string;
      placeholderMcpTitle: string;
      placeholderMcpDesc: string;
    };
    skills: {
      title: string;
      localTab: string;
      marketTab: string;
      localTitle: string;
      localDesc: string;
      catalogCount: (count: number) => string;
      searchAria: string;
      searchPlaceholder: string;
      loading: string;
      loadFailed: (msg: string) => string;
      all: string;
      other: string;
      visibleCount: (label: string, count: number) => string;
      totalCount: (count: number) => string;
      enabledCount: (count: number) => string;
      noDescription: string;
      noMatch: (query: string) => string;
      toggleSkillAria: (enabled: boolean, name: string) => string;
      localSource: string;
      runtimeSource: string;
      categoryLabels: Record<string, string>;
    };
    closeAria: string;
  };

  // Agent Market
  agentWorld: {
    myAgents: string;
    store: string;
    featured: string;
    popular: string;
    discover: string;
    searchPlaceholder: string;
    categories: {
      all: string;
      assistant: string;
      coder: string;
      researcher: string;
      creative: string;
      automation: string;
      specialist: string;
      financial: string;
    };
    searchAgents: string;
    noAgentsFound: string;
    noFeatured: string;
    noPopular: string;
    title: string;
    description: string;
    pageOf: (page: number, total: number) => string;
    by: string;
    agentInstalled: string;
    agentUninstalled: string;
    installThisAgent: string;
    assembleCapabilityPack: string;
    keySkillCount: (count: number) => string;
    emptyState: string;
    // AgentWorldUnified extras
    createAgentCardTitle: string;
    createAgentCardDesc: string;
    addAgent: string;
    newAgent: string;
    discoverTagline: string;
    // AgentWorldCard extras
    toastInstalled: (name: string) => string;
    toastCapabilityPackInstalled: (name: string, count: number) => string;
    toastUninstalled: (name: string) => string;
    installAriaLabel: (name: string) => string;
    uninstallAriaLabel: (name: string) => string;
    ratingAriaLabel: (rating: string, count: number) => string;
    downloadCountAriaLabel: (count: string) => string;
    authorPrefix: string;
  };

  // Agent World Unified
  agentWorldUnified: {
    pageTitle: string;
    pageDescription: string;
    addAgentButton: string;
    roleLibrary: string;
    roleLibraryDescription: string;
    installedLabel: string;
    installableLabel: string;
    installAllButton: string;
    installAllConfirmButton: (count: number) => string;
    installAllConfirmTitle: (count: number) => string;
    installAllConfirmHint: string;
    installSuccess: (installed: number) => string;
    installSuccessWithFailure: (installed: number, failed: number) => string;
    installFailed: string;
    enterprise: string;
    localTab: string;
    enabledTab: string;
    marketplaceTab: string;
    categoryFilterLabel: string;
    domainFilterLabel: string;
    domains: {
      all: string;
      general: string;
      coding: string;
      research: string;
      creative: string;
      automation: string;
      ecommerce: string;
      finance: string;
    };
    loadingAgents: string;
    loadAgentsFailed: string;
    retryAgents: string;
    searchPlaceholderAgents: string;
    searchPlaceholderPlugins: string;
    searchPlaceholderSkills: string;
  };

  // Local Agent Connect Dialog

  // Agent World Card
  agentWorldCard: {};

  // Community
  community: {
    searchDiscussions: string;
    activeAgents: string;
    totalLikes: string;
    totalDiscussions: string;
    noPosts: string;
  };

  // Feed
  feed: {
    noActivity: string;
    newAgentPublished: string;
    agentTrending: string;
    reached: string;
    downloads: string;
  };

  // Browser
  browser: {
    back: string;
    forward: string;
    reload: string;
    urlPlaceholder: string;
    closeSession: string;
    actionLog: string;
    noActions: string;
    launchBrowser: string;
    launchingBrowser: string;
    browserAutomation: string;
    browserAutomationDesc: string;
    navigateHint: string;
    openExternal: string;
    clickToRefresh: string;
    actions: (count: number) => string;
    stopAutoRefresh: string;
    startAutoRefresh: string;
    toggleDevice: string;
    deviceDesktop: string;
    deviceTablet: string;
    deviceMobile: string;
    viewportHint: (label: string, w: number, h: number) => string;
    startBrowsingHint: string;
    loadingPage: string;
    embeddedBlocked: string;
    embeddedBlockedDescription: string;
    assistant: {
      stopAgent: string;
      stopAgentTooltip: string;
      autoBrowseOnTooltip: string;
      autoBrowseOffTooltip: string;
      summarizePage: string;
      extractKeyPoints: string;
      translateToChinese: string;
      recorderTitle: string;
      recorderDesc: string;
      researchGoalPlaceholder: string;
      start: string;
      recordCurrentPage: string;
      clearLog: string;
      copied: string;
      copyBrief: string;
      exportMd: string;
      emptyHint: string;
      thinking: string;
      inputPlaceholder: string;
      noAgents: string;
      confirmInputContent: string;
      confirmSubmitForm: string;
      confirmSensitiveClick: string;
      confirmSensitiveAction: string;
      stopAgentMessage: string;
      maxLoopReached: (count: number) => string;
      webviewNotReadyError: string;
      confirmedRiskyOperation: string;
      recorderProtocol: string;
      researchMissionLabel: string;
      researchPlatformDivisionLabel: string;
      researchExecutionRequirementsLabel: string;
      researchRequirementOpenFirstPlatform: string;
      researchRequirementExtractHighDensity: string;
      researchRequirementDoNotFeedBack: string;
      researchRequirementLogPerPlatform: string;
      researchRequirementPauseForSensitive: string;
      researchPlatformHintGemini: string;
      researchPlatformHintNotebookLM: string;
      researchPlatformNameDoubao: string;
      researchPlatformHintDoubao: string;
      researchPlatformHintPerplexity: string;
      researchLogDispatchLabel: string;
      researchStartTitle: string;
      researchPlatformsPrefix: string;
      currentPageFallback: string;
      recordedPageNote: string;
      needElectronError: string;
      tabNotReadyError: string;
      summarizePagePrompt: string;
      extractKeyPointsPrompt: string;
      translateToChinesePrompt: string;
      currentPageLabel: string;
      urlLabel: string;
      titleLabel: string;
      pageAgentCapabilityLabel: string;
      truncatedSuffix: (count: number) => string;
      needsUserConfirmationTitle: string;
      confirmExecute: string;
      researchBriefTitle: string;
      researchBriefGeneratedAt: (time: string) => string;
      researchBriefRecordCount: (count: number) => string;
      researchBriefAbstractRecords: string;
      researchBriefEntryTime: (time: string) => string;
      researchBriefEntryRecordLabel: string;
      researchBriefPendingVerification: string;
      researchBriefVerifyCrossPlatform: string;
      researchBriefKeepEvidence: string;
      researchBriefConfirmSensitive: string;
      unknownPlatform: string;
    };
    copilot: {
      stopAgent: string;
      stopAgentTooltip: string;
      autoBrowseOnTooltip: string;
      autoBrowseOffTooltip: string;
      summarizePage: string;
      extractKeyPoints: string;
      translateToChinese: string;
      recorderTitle: string;
      recorderDesc: string;
      researchGoalPlaceholder: string;
      start: string;
      recordCurrentPage: string;
      clearLog: string;
      copied: string;
      copyBrief: string;
      exportMd: string;
      emptyHint: string;
      thinking: string;
      inputPlaceholder: string;
      noAgents: string;
      confirmInputContent: string;
      confirmSubmitForm: string;
      confirmSensitiveClick: string;
      confirmSensitiveAction: string;
    };

    extensionMarketplace: {
      title: string;
      subtitle: string;
      installLocal: string;
      refreshAriaLabel: string;
      closeAriaLabel: string;
      searchPlaceholder: string;
      installedExtensions: string;
      electronSupported: string;
      webPreviewOnly: string;
      webPreview: string;
      enabled: string;
      disabled: string;
      removeAriaLabel: (name: string) => string;
      noExtensionsElectron: string;
      noExtensionsWeb: string;
      comingSoonBadge: string;
      installedBadge: string;
      installableBadge: string;
      install: string;
      rating: string;
      installs: string;
      status: string;
      capabilityTags: string;
      errorListFailed: string;
      errorInstallFailed: string;
      errorStatusFailed: string;
      errorRemoveFailed: string;
      confirmRemove: string;
      categoryFeatured: string;
      categoryEfficiency: string;
      categoryResearch: string;
      categorySecurity: string;
      categoryDevelopment: string;
      categoryComingSoon: string;
      taglinePageAgent: string;
      taglineResearchClipper: string;
      taglineShieldLite: string;
      taglineCookieVault: string;
      taglineTranslatorLens: string;
      taglineDomInspector: string;
      taglineVisualRecorder: string;
      tagsPageAgent: string[];
      tagsResearchClipper: string[];
      tagsShieldLite: string[];
      tagsCookieVault: string[];
      tagsTranslatorLens: string[];
      tagsDomInspector: string[];
      tagsVisualRecorder: string[];
    };
    webviewTab: {
      extPluginButton: string;
      openDirectory: string;
      extPluginTitle: string;
      extPluginDesc: string;
      dragToBookmarks: string;
      dragToBookmarksDesc: string;
      dragToBookmarksTitle: string;
      step1Temporary: string;
      step2LongTerm: string;
      step3LoadExtension: string;
      pluginDirectory: string;
      pluginDirectoryOpened: (path: string) => string;
      pluginPathCopied: string;
      bookmarkletCopied: string;
      copyBookmarklet: string;
      copyPath: string;
      openPluginDirectory: string;
      connectingPlugin: string;
      connectingPluginDesc: string;
      searchPlaceholder: string;
      resetLayout: string;
      finishEditing: string;
      editDesktop: string;
      dragHint: string;
      aiBrowserDesktop: string;
      calendarLabel: string;
      aiToolFolder: string;
      researchWidgets: string;
      addTitle: string;
      settingsTitle: string;
      weekdays: string[];
      monthFormat: (year: number, month: number) => string;
      navHome: string;
      navTheme: string;
      navWidgets: string;
      navWallpaper: string;
      navGames: string;
      appNameCocoloopCommunity: string;
      appNameCocoloopMarket: string;
      appDescCocoloopCommunity: string;
      appDescCocoloopMarket: string;
      appDescGemini: string;
      appDescNotebookLM: string;
      appDescDoubao: string;
      appDescPerplexity: string;
      appDescChatGPT: string;
      appDescClaude: string;
      appDescKimi: string;
      widgetTitleResearch: string;
      widgetSubtitleResearch: string;
      widgetTitleTodayTasks: string;
      widgetSubtitleTodayTasks: string;
      panelTitleTheme: string;
      panelTitleWidgets: string;
      panelTitleWallpaper: string;
      panelTitleGames: string;
      panelTitleAddApp: string;
      panelTitleDesktopSettings: string;
      panelSubtitle: string;
      panelClose: string;
      themeNames: string[];
      themeDescs: string[];
      widgetPanelNames: string[];
      widgetPanelDescs: string[];
      widgetEnabled: string;
      wallpaperTitle: (index: number) => string;
      gameNames: string[];
      settingNames: string[];
      settingDescs: string[];
      crashTitle: string;
      crashDesc: string;
      crashReload: string;
      ctxEditIcon: string;
      ctxDelete: string;
      ctxSizeSmall: string;
      ctxSizeMedium: string;
      ctxSizeLarge: string;
      ctxEditWidget: string;
      ctxEditFolder: string;
      ctxEditHome: string;
      ctxAddWidget: string;
      ctxAddIcon: string;
      ctxSettings: string;
      editWidgetDialogTitle: string;
      editWidgetTitleLabel: string;
      editWidgetTitlePlaceholder: string;
      editWidgetTypeLabel: string;
      editWidgetTypeWeather: string;
      editWidgetTypeCalendar: string;
      editWidgetTypeNotes: string;
      editWidgetTypeSystem: string;
      editWidgetTypeAiTools: string;
      editWidgetTypeBookmarks: string;
      editWidgetSave: string;
      promptSiteName: string;
      promptSiteUrl: string;
      promptFolderName: string;
      searchPlaceholderFormat: (engine: string) => string;
      searchEngineFallback: string;
      newWidget: string;
      newFolder: string;
      addIconBtn: string;
      addWidgetBtn: string;
      appNameDoubao: string;
      deleteConfirmTitle: string;
      deleteConfirmDescription: string;
      resetLayoutConfirmTitle: string;
      resetLayoutConfirmDescription: string;
    };
    empty: {
      noMatch: string;
      noTabs: string;
      noRecent: string;
      noFavorites: string;
    };
    defaultTabTitle: string;
    pageTitle: string;
    pageSubtitle: (pinned: boolean) => string;
    searchPlaceholder: string;
    copy: {
      link: string;
      title: string;
      copied: string;
      tabMenuItem: string;
    };
    menu: {
      closeOtherTabs: string;
    };
    tabs: {
      label: string;
      recent: string;
      favorites: string;
    };
    newTab: string;
    newTabPage: string;
    closeTab: string;
    sidePanel: {
      unpin: string;
      expand: string;
    };
    tabBar: {
      close: string;
      newTab: string;
      homeTabShort: string;
    };
    urlBar: {
      back: string;
      forward: string;
      refresh: string;
      backToHome: string;
      searchOrUrl: string;
      siteInfo: string;
      siteInfoDesc: string;
      clearData: string;
      openExternally: string;
      confirmClearSiteData: string;
      clearingSiteData: string;
      clearFailed: string;
      siteCleared: (origin: string) => string;
      currentSite: string;
      addBookmark: string;
      removeBookmark: string;
      bookmarkLabel: string;
      historyLabel: string;
      searchOrOpen: (query: string) => string;
      downloads: string;
      downloading: string;
      recentDownload: string;
      downloadCount: (count: number) => string;
      noDownloads: string;
      noDownloadRecords: string;
      unnamedDownload: string;
      downloadCompleted: string;
      downloadIncomplete: string;
      unknownSize: string;
      openFile: string;
      openFolder: string;
      historyAndBookmarks: string;
      bookmarksTab: (count: number) => string;
      historyTab: (count: number) => string;
      clearHistoryTitle: string;
      clearHistory: string;
      noBookmarks: string;
      noHistory: string;
      removeBookmarkTitle: string;
      deviceDesktop: string;
      deviceTablet: string;
      deviceMobile: string;
      switchToDevice: (device: string) => string;
      browserExtensions: string;
      openBrowserExtensions: string;
      extensionsLabel: string;
      aiAssistant: string;
      moreActions: string;
      pageActions: string;
      findInPage: string;
      findPrompt: string;
      zoom: string;
      zoomOut: string;
      zoomIn: string;
      resetZoom: string;
      devicePreview: string;
    };
    closeFolderAria: (name: string) => string;
  };

  // Gene Lock Badge
  geneLockBadge: {
    levelNames: string[];
    levelDescriptions: string[];
    badgeTitle: string;
    badgeLabel: string;
    panicBadge: string;
    productionBadge: string;
    dropdownTitle: string;
    modeLabel: string;
    maturityLabel: string;
    panicActive: string;
    panicStartedAt: string;
    panicReason: string;
    unlockButton: string;
    panicButton: string;
    panicConfirm: string;
    levelSummary: (level: number, name: string, description: string) => string;
    compactTitle: string;
    modeRelaxed: string;
    modeStrict: string;
    strictHint: string;
    relaxedHint: string;
    modeDescription: string;
    evolutionPaused: string;
    settingsTitle: string;
    settingsDescription: string;
    openModeLabel: string;
    levelLabel: string;
    masterSwitchLabel: string;
    disableEvolutionButton: string;
    disabledHint: string;
    updateSuccess: string;
    operationFailed: string;
  };

  // Browser Preview Panel
  browserPreviewPanel: {
    desktopLabel: string;
    actionPending: string;
    actionSuccess: string;
    actionFailed: string;
    coordinateLabel: (coord: string) => string;
    noDetail: string;
    livePreviewTitle: string;
    toggleSurfaceMode: string;
    surfaceModeLive: string;
    surfaceModeScreenshot: string;
    selectDevicePreset: string;
    continueInFullBrowser: string;
    takeoverButton: string;
    switchToLivePreview: string;
    switchToLivePreviewDescription: string;
    switchToScreenshot: string;
    switchToScreenshotDescription: string;
    sessionHealthyLabel: string;
    sessionAttentionLabel: string;
    endSession: string;
    annotateScreenshot: string;
    annotationButton: string;
    annotationPlaceholder: string;
    annotationInputLabel: string;
    sendAnnotation: string;
    cancelAnnotation: string;
    sessionNeedsAttention: (issues: string) => string;
    reconnectButton: string;
    semanticSnapshotFallback: string;
    truncatedBadge: string;
    closeSemanticSnapshot: string;
    noReadableText: string;
    loadingLivePage: string;
    screenshotClickTitle: (mode: string, viewport: string) => string;
    clickMode: string;
    doubleClickMode: string;
    localServices: string;
    noLocalServices: string;
    scanButton: string;
    serviceTypeFrontend: string;
    serviceTypeBackend: string;
    serviceTypeOther: string;
    scanLocalServices: string;
    localPreviewMode: string;
    localPreviewRunning: (port: string) => string;
    localPreviewRefresh: string;
    localPreviewOpenExternal: string;
    selectedAction: (action: string) => string;
    locateActionTitle: string;
    deselectTitle: string;
    failureCount: (count: number) => string;
    coordinateCount: (count: number) => string;
    attachScreenshotToComposer: string;
    attachScreenshotSource: string;
    attachScreenshotSuccess: string;
    attachScreenshotFailed: string;
  };

  // Browser Home
  browserHome: {
    appNameDoubao: string;
    appNameTongyiQianwen: string;
    appNameWenxinYiyan: string;
    appNameTencentYuanbao: string;
    appNameZhihu: string;
    appDescGemini: string;
    appDescNotebookLM: string;
    appDescDoubao: string;
    appDescDeepSeek: string;
    appDescTongyiQianwen: string;
    appDescWenxinYiyan: string;
    appDescTencentYuanbao: string;
    appDescPerplexity: string;
    appDescChatGPT: string;
    appDescClaude: string;
    appDescKimi: string;
    appDescAgnesAi: string;
    appDescYouTube: string;
    appDescBilibili: string;
    appDescGitHub: string;
    appDescStackOverflow: string;
    appDescMdn: string;
    appDescZhihu: string;
    appDescWikipedia: string;
    groupAiTools: string;
    groupAiToolsSubtitle: string;
    groupVideo: string;
    groupVideoSubtitle: string;
    groupDev: string;
    groupDevSubtitle: string;
    groupKnowledge: string;
    groupKnowledgeSubtitle: string;
    searchEngineBaidu: string;
    searchEngineBaiduIcon: string;
    metaBookmark: string;
    metaRecent: string;
    metaCommon: string;
    categoryVideo: string;
    categoryDev: string;
    switchSearchEngine: string;
    commonCategories: string;
    recentVisits: string;
    recentVisitCount: (count: number) => string;
    historyOnly: string;
    noRecentVisits: string;
    commonEntries: string;
    addToDock: string;
    todoPlaceholder: string;
    removeFromDock: string;
    alreadyInDock: string;
    add: string;
  };

  // Execution Plan
  executionPlan: {
    title: string;
    awaitingReview: string;
    executing: string;
    completed: string;
    rejected: string;
    lowRisk: string;
    mediumRisk: string;
    highRisk: string;
    approve: string;
    reject: string;
    edit: string;
    fast: string;
    medium: string;
    slow: string;
    addStep: string;
    confirmReject: string;
    cancel: string;
    saveAndReview: string;
    editPlan: string;
    toastApproved: string;
    toastApproveFailed: string;
    toastMustHaveStep: string;
    toastUpdated: string;
    toastModifyFailed: string;
    toastRejected: string;
    toastRejectFailed: string;
    removeStepAria: string;
    stepDescriptionAria: string;
  };

  // Mode Selector
  modes: {
    builder: string;
    coder: string;
    develop: string;
    audit: string;
    uxui: string;
    architect: string;
    ultra: string;
    standard: string;
    teamCoder: string;
    admin: string;
    builderTooltip: string;
    coderTooltip: string;
    developTooltip: string;
    auditTooltip: string;
    uxuiTooltip: string;
    architectTooltip: string;
    ultraTooltip: string;
    teamCoderTooltip: string;
    adminTooltip: string;
    builderDesc: string;
    coderDesc: string;
    developDesc: string;
    auditDesc: string;
    uxuiDesc: string;
    architectDesc: string;
    ultraDesc: string;
    builderEffect: string;
    coderEffect: string;
    developEffect: string;
    auditEffect: string;
    uxuiEffect: string;
    architectEffect: string;
    ultraEffect: string;
    strategyNote: string;
    autoShort: string;
    manualOverrideShort: string;
    autoDetectedMode: (mode: string) => string;
    autoDetecting: string;
    autoModeNote: string;
    autoModeNoteCompact: string;
    manualOverrideNote: string;
    followAuto: string;
    projectKind: string;
    projectKindNew: string;
    projectKindExisting: string;
    projectKindArchitecture: string;
    teamCoderDesc: string;
    adminDesc: string;
    autoDetected: string;
    projectTemplates: string;
    projectSignals: string;
    viewDetectionBasis: string;
    signalFiles: (count: number) => string;
    signalTechStack: (count: number) => string;
    signalLocks: (count: number) => string;
    signalCommits: (count: number) => string;
    signalReadme: string;
    signalSummaryEmpty: string;
  };

  // Intent-based mode auto-switch
  modeIntent: {
    suggestSwitch: (modeLabel: string) => string;
    switch: string;
    ignore: string;
    autoSwitched: (modeLabel: string) => string;
  };

  // Code page tabs
  codeTabs: {
    monitor: string;
    traces: string;
    arena: string;
    teach: string;
    browser: string;
    diff: string;
  };

  // Editor Tabs
  editorTabs: {
    closeTabAria: (label: string) => string;
  };

  // Skills Market
  skillsMarket: {
    title: string;
    categories: {
      all: string;
      coding: string;
      writing: string;
      research: string;
      automation: string;
      data: string;
      devops: string;
      security: string;
      other: string;
    };
    searchPlaceholder: string;
    enabled: string;
    disabled: string;
    enable: string;
    disable: string;
    noDescription: string;
    builtIn: string;
    skillsCount: (total: number, enabled: number) => string;
    filterAll: (count: number) => string;
    filterInstalled: (count: number) => string;
    filterEnabled: (count: number) => string;
    loadingSkills: string;
    noSkillsFound: string;
    noSkillsFoundHint: string;
    description: string;
    requiredTools: string;
    parameters: string;
    usageExamples: string;
    systemPrompt: string;
    exportJson: string;
    downloads: (count: number) => string;
    input: string;
    expected: string;
    createCustomSkill: string;
    createCustomSkillDesc: string;
    importSkill: string;
    importSkillDesc: string;
    name: string;
    category: string;
    tags: string;
    tagsPlaceholder: string;
    systemPromptPlaceholder: string;
    nameAndPromptRequired: string;
    failedToCreate: string;
    importSource: string;
    importFailed: string;
    publishAgentAsApi: string;
    createSkill: string;
  };

  // API Publish
  apiPublish: {
    title: string;
    tabs: {
      code: string;
      keys: string;
      logs: string;
      stats: string;
      test: string;
    };
    publishAgentAsApi: string;
    apiName: string;
    agent: string;
    endpointPath: string;
    endpointPathHint: string;
    rpmLimit: string;
    dailyLimit: string;
    publish: string;
    endpoint: string;
    noPublishedApis: string;
    noPublishedApisHint: string;
    generateKey: string;
    keyName: string;
    copyKeyWarning: string;
    noApiKeys: string;
    recentCalls: (count: number) => string;
    noCallsYet: string;
    loadingStats: string;
    totalCalls: string;
    today: string;
    avgLatency: string;
    totalTokens: string;
    success: string;
    errors: string;
    dailyCalls: string;
    apiKey: string;
    inputLabel: string;
    inputPlaceholder: string;
    sendRequest: string;
    response: string;
    revoke: string;
    revokeKeyConfirmTitle: string;
    revokeKeyConfirmDescription: string;
    deleteApiConfirmTitle: string;
    deleteApiConfirmDescription: string;
    disable: string;
    enable: string;
    refreshTooltip: string;
    cancel: string;
    created: string;
    lastUsed: string;
  };

  // Deploy (extend existing)
  deploy: {
    title: string;
    detecting: string;
    building: string;
    deploying: string;
    ready: string;
    error: string;
    cancelled: string;
    recommended: string;
    generatedConfigs: string;
    openDeployment: string;
    copyUrl: string;
    setup: string;
    history: string;
    analyzingProject: string;
    deployTarget: string;
    deployTo: (provider: string) => string;
    detectProject: string;
    clickDetectHint: string;
    pending: string;
    unknown: string;
    details: string;
    deployAgain: string;
    noActiveDeployment: string;
    noActiveDeploymentHint: string;
    noDeployments: string;
    noDeploymentsHint: string;
    requires: string;
    confidence: string;
    build: string;
    output: string;
    port: string;
    staticPreview: string;
    openInBrowser: string;
  };

  // Codebase Index
  codebaseIndex: {
    title: string;
    tabs: {
      search: string;
      status: string;
      stats: string;
    };
    searchPlaceholder: string;
    indexIncremental: string;
    rebuildFull: string;
    clearIndex: string;
    notIndexed: string;
    notIndexedHint: string;
    startIndexing: string;
    noMatchingCode: string;
    indexingInProgress: string;
    preparing: string;
    files: string;
    chunks: string;
    embedded: string;
    skipped: string;
    elapsed: string;
    progress: string;
    errors: (count: number) => string;
    indexingComplete: string;
    completedIn: string;
    indexReady: string;
    noIndex: string;
    indexReadyHint: string;
    noIndexHint: string;
    dbSize: string;
    configuration: string;
    backend: string;
    model: string;
    chunkSize: string;
    autoIndex: string;
    on: string;
    off: string;
    languages: string;
    chunkTypes: string;
    lastIndexed: string;
    showCode: string;
    hideCode: string;
    toastFullReindexStarted: string;
    toastIncrementalStarted: string;
    toastStartIndexingFailed: string;
    toastIndexCleared: string;
    toastClearIndexFailed: string;
    clearIndexConfirmTitle: string;
    clearIndexConfirmDescription: string;
  };

  // Teach & Repeat
  teachRepeat: {
    title: string;
    recording: string;
    stop: string;
    startRecording: string;
    startRecordingDesc: string;
    workflowName: string;
    workflowNamePlaceholder: string;
    descriptionOptional: string;
    newRecording: string;
    backToLibrary: string;
    searchWorkflows: string;
    noWorkflows: string;
    noWorkflowsHint: string;
    noMatchingWorkflows: string;
    replay: string;
    adaptiveReplay: string;
    replayResults: string;
    replayingWorkflow: string;
    steps: string;
    params: string;
    used: string;
    noDescription: string;
    duplicate: string;
    deleteConfirmTitle: string;
    deleteConfirmDescription: (name: string) => string;
    deleteConfirmDescriptionUnknown: string;
  };

  // Parallel Agents
  parallelAgents: {
    title: string;
    active: string;
    max: string;
    noParallelTasks: string;
    noParallelTasksHint: string;
    completed: string;
    failed: string;
    cancelled: string;
    batches: string;
    filterAgents: string;
    noMatchingAgents: string;
    waitingForAgents: string;
    aggregatedResults: string;
    rawResults: string;
    aggregated: string;
    conflictsDetected: (count: number) => string;
    noOutput: string;
    agentsRunning: (count: number) => string;
    noActiveTasks: string;
    cancelAll: string;
    depends: string;
    recoveryReady: string;
    coordinationSummary: string;
    coordinationAction: (action: string) => string;
    primaryTask: (taskId: string) => string;
    cancelledTasks: (count: number) => string;
    coordinationWarnings: (count: number) => string;
    rerunnableTasks: (count: number) => string;
    failedTasks: (count: number) => string;
    dependencyBlocked: (count: number) => string;
    checkpointSequence: (sequence: number) => string;
    recoverySafe: string;
    recoveryUnsafe: string;
    statusLabels: {
      pending: string;
      running: string;
      completed: string;
      failed: string;
      cancelled: string;
      timedOut: string;
      partial: string;
    };
  };

  // Monitor
  monitor: {
    title: string;
    tokens: string;
    estCost: string;
    turns: string;
    toolCalls: string;
    cacheReads: string;
    tokensCached: string;
    unique: string;
    toolUsage: string;
    noToolCalls: string;
    telemetry: string;
    otelEnabled: string;
    otelDisabled: string;
    metrics: string;
    privacy: string;
    promptsRedacted: string;
    promptsLogged: string;
    tokenDistribution: string;
    inputLabel: string;
    outputLabel: string;
    cacheLabel: string;
  };

  // Traces
  traces: {
    title: string;
    trace: string;
    spans: string;
    total: string;
    noTraces: string;
    attributes: string;
    events: (count: number) => string;
    refresh: string;
  };

  // Collab
  collab: {
    invite: string;
    inviteCollaborators: string;
    collaborateTitle: string;
    collaborateDesc: string;
    generatingLink: string;
    clickToGenerate: string;
    generateInviteLink: string;
    copied: string;
    copy: string;
    copyLink: string;
    currentlyOnline: (count: number) => string;
    online: string;
    typing: string;
    // InviteDialog extras
    inviteDialogTitle: string;
    inviteDialogDesc: string;
    linkCopied: string;
    copyFailed: string;
    // PresenceAvatars
    onlineCount: (count: number) => string;
    defaultTeamName: string;
    projectPrefix: (teamName: string) => string;
    teamModes: Array<{
      id: string;
      label: string;
      description: string;
    }>;
    common: {
      online: string;
      offline: string;
      leader: string;
      aiMember: string;
      cancel: string;
      create: string;
      loading: string;
    };
    workbench: {
      tabMembers: string;
      tabTasks: string;
      tabWorkspace: string;
      title: string;
      closeTitle: string;
      leaderStandby: string;
      standby: string;
      memberNameWithRole: (name: string, isLeader: boolean) => string;
      currentWorkspace: string;
      noDirectorySelected: string;
    };
    roster: {
      title: string;
      noTeamSelected: string;
      aiMembersCount: (count: number) => string;
      onlineCount: (online: number, total: number) => string;
      workstationGroup: string;
      aiMemberDefault: string;
      standby: string;
      collaboratorsGroup: string;
      emptyHint: string;
      statusWithRole: (status: string, role: string) => string;
    };
    createTask: {
      toastCreated: string;
      toastFailed: string;
      title: string;
      description: string;
      taskTitleLabel: string;
      descriptionLabel: string;
      sopLabel: string;
      assigneeLabel: string;
      titlePlaceholder: string;
      descriptionPlaceholder: string;
      autoMatchFreeform: string;
      loadingPacks: string;
      cancel: string;
      create: string;
    };
    inviteAgents: {
      toastAdded: (count: number) => string;
      toastFailed: string;
      roleMember: string;
      roleMemberDesc: string;
      roleViewer: string;
      roleViewerDesc: string;
      addAgentTitle: string;
      countText: (inTeam: number, available: number) => string;
      addFiltered: string;
      searchPlaceholder: string;
      loadingAgents: string;
      noMatches: string;
      inTeam: string;
      add: string;
    };
    humanInvite: {
      trigger: string;
      dialogTitle: string;
      dialogDescription: string;
      roleLabel: string;
      expiresLabel: string;
      expiresHour: string;
      expiresDay: string;
      expiresWeek: string;
      expiresMonth: string;
      createLink: string;
      creatingLink: string;
      currentLink: string;
      linkVisibleOnce: string;
      recordsTitle: string;
      refresh: string;
      emptyRecords: string;
      loadingRecords: string;
      createFailed: string;
      loadFailed: string;
      revoke: string;
      revokeSuccess: string;
      revokeFailed: string;
      statusActive: string;
      statusExpired: string;
      statusExhausted: string;
      statusRevoked: string;
      neverExpires: string;
      expiresAt: (value: string) => string;
      usage: (used: number, max: number | null) => string;
      roomRequired: string;
      joinPolicyLabel: string;
      joinPolicyApply: string;
      joinPolicyApplyDesc: string;
      joinPolicyDirect: string;
      joinPolicyDirectDesc: string;
      directJoinConfirmTitle: string;
      directJoinConfirmDescription: string;
      directJoinConfirmAction: string;
      directJoinConfirmCancel: string;
      policySaveFailed: string;
      pendingRequestsTitle: string;
      pendingRequestsEmpty: string;
      requestsLoadFailed: string;
      approveRequest: string;
      rejectRequest: string;
      approveSuccess: string;
      rejectSuccess: string;
      requestActionFailed: string;
    };
    mobileJoin: {
      title: string;
      description: string;
      connectCodeLabel: string;
      manualFillPrefix: string;
      manualFillCode: string;
    };
  };

  // Agent detail/profile
  agentDetail: {
    overview: string;
    profile: string;
    memory: string;
    reviews: string;
    social: string;
    chat: string;
    install: string;
    installed: string;
    uninstall: string;
    writeReview: string;
    submitReview: string;
    submit: string;
    noReviews: string;
    conversations: string;
    messages: string;
    satisfaction: string;
    responseTime: string;
    addMemory: string;
    deleteMemory: string;
    relationships: string;
    strength: string;
    downloads: string;
    ratings: string;
    description: string;
    tags: string;
    capabilities: string;
    tasksCompleted: string;
    profileNotAvailable: string;
    noMemories: string;
    noRelationships: string;
    agentProfileAndMemory: string;
    statistics: string;
    memoryContent: string;
    noMemoriesYet: string;
    noRelationshipsYet: string;
    shareExperience: string;
    reviewSubmitted: string;
    memoryAdded: string;
    memoryDeleted: string;
    confidence: string;
    accessed: string;
    official: string;
    featured: string;
    memoryTypes: {
      fact: string;
      preference: string;
      learned_skill: string;
      relationship: string;
    };
    journal: string;
    noJournal: string;
    journalIntro: string;
    journalMoods: {
      insight: string;
      mistake: string;
      pride: string;
      tired: string;
      question: string;
    };
  };

  // MCP Settings page
  mcpSettings: {
    title: string;
    description: string;
    trustedTag: string;
    untrustedTag: string;
    trustButton: string;
    revokeButton: string;
    revokeConfirmTitle: string;
    revokeConfirmDescription: (name: string) => string;
    unapprovedHint: string;
    noServers: string;
    toastLoadConfigFailed: string;
    toastTrustSuccess: (name: string) => string;
    toastTrustFailed: string;
    toastRevokeSuccess: (name: string) => string;
    toastRevokeFailed: string;
    toastToggleSuccess: (name: string, enabled: boolean) => string;
    toastUpdateFailed: string;
    addRemoteTitle: string;
    addNamePlaceholder: string;
    addUrlPlaceholder: string;
    addAuthPlaceholder: string;
    addButton: string;
    toastAddSuccess: (name: string) => string;
    toastAddFailed: string;
    toastAddInvalid: string;
  };

  // Intelligence
  intelligence: {
    title: string;
    runNow: string;
    latestReport: string;
    history: string;
    config: string;
    noReports: string;
    running: string;
    sources: string;
    itemsAnalyzed: string;
    skillsCreated: string;
    reports: string;
    enabled: string;
    disabled: string;
    schedule: string;
    lastRun: string;
    subscriptions: string;
    addSubscription: string;
    customTopic: string;
    keywords: string;
    selectSources: string;
    stubWarning: string;
    enabledTopics: string;
    runAll: string;
    topicReport: string;
    viewHistory: string;
    noSubscriptions: string;
    reportOverview: string;
    reportOverviewDescription: string;
    repoSpotlights: string;
    repoSpotlightsRankedBy: string;
    fullReport: string;
    fullReportDescription: string;
    spotlightsButton: string;
    backToTop: string;
    reportLanguage: string;
    reportLanguageZh: string;
    reportLanguageEn: string;
    reportLanguageBoth: string;
    // IntelligencePanel extras
    subscriptionsHeader: string;
    addButton: string;
    noSubscriptionsHint: (keywordExample: string) => string;
    exampleKeyword: string;
    keywordsPrefix: string;
    lastRunPrefix: (date: string) => string;
    neverRun: string;
    reportsHeader: string;
    noReportsHint: string;
    itemsCount: (n: number) => string;
    loadFailed: string;
    retry: string;
    subscriptionAdded: string;
    addFailed: string;
    updateFailed: string;
    // Additional keys for IntelligencePanel
    subscriptionDeleted: string;
    deleteFailed: string;
    runSubscriptionFailed: string;
    runAllSubscriptionsFailed: string;
    reportGenerated: (count: number) => string;
    reportsGenerated: (count: number) => string;
    aiCustomSubscription: string;
    aiCustomSubscriptionDescription: string;
    collapse: string;
    expand: string;
    generateDraft: string;
    createSubscription: string;
    draftPlaceholder: string;
    deleteSubscription: string;
    deleteConfirmTitle: string;
    deleteConfirmDescription: (name: string) => string;
    selectSubscription: (name: string) => string;
    runSubscription: (name: string) => string;
    enableSubscription: (name: string) => string;
    disableSubscription: (name: string) => string;
    deleteSubscriptionNamed: (name: string) => string;
    source: string;
    web: string;
    // Automation tabs
    configuredTip: string;
    configuredTipToggle: string;
    configuredEmptyTitle: string;
    configuredEmptyDescription: string;
    createCustomTask: string;
    useTemplate: string;
    // Create automation dialog
    nameRequired: string;
    topicRequired: string;
    createTaskSuccess: string;
    createTaskFailed: string;
    createTaskTitle: string;
    createTaskDescription: string;
    taskNameLabel: string;
    taskNamePlaceholder: string;
    topicLabel: string;
    topicPlaceholder: string;
    cadenceLabel: string;
    cadenceHourly: string;
    scheduleTimeLabel: string;
    scheduleDayLabel: string;
    instructionsLabel: string;
    instructionsPlaceholder: string;
    createTask: string;
    // Automation history
    historyItemsAnalyzed: (n: number) => string;
    historyErrors: (n: number) => string;
    historyCollapse: string;
    historyViewDetails: string;
    historyEmptyTitle: string;
    historyEmptyDescription: string;
  };

  intelligencePanel: {
    examplePrompts: string[];
    goalLabel: string;
    goalPlaceholder: string;
    subscriptionName: string;
    keywords: string;
    cadence: string;
    sources: string;
    instructions: string;
    cadenceHighFrequency: string;
    cadenceDaily: string;
    cadenceWeekly: string;
    cadenceMonthly: string;
    weekdayMonday: string;
    weekdayTuesday: string;
    weekdayWednesday: string;
    weekdayThursday: string;
    weekdayFriday: string;
    weekdaySaturday: string;
    weekdaySunday: string;
    reportWeekdaySunday: string;
    reportWeekdayMonday: string;
    reportWeekdayTuesday: string;
    reportWeekdayWednesday: string;
    reportWeekdayThursday: string;
    reportWeekdayFriday: string;
    reportWeekdaySaturday: string;
    scheduleHighFrequency: (timezone: string) => string;
    scheduleWeekly: (weekday: string, time: string, timezone: string) => string;
    scheduleMonthly: (day: string, time: string, timezone: string) => string;
    scheduleDaily: (time: string, timezone: string) => string;
    monthDayLabel: (day: string) => string;
    keyFindingsHeading: string;
    recommendationsHeading: string;
    aiGenerated: string;
    itemsCount: (count: number) => string;
    skillsCount: (count: number) => string;
    view: string;
    runTime: string;
    monthlyDate: string;
    weeklyDate: string;
    timezone: string;
    expectedRun: (schedule: string) => string;
    noSubscriptionsYet: string;
    latestUpdates: string;
    sortedBySubscriptionPush: string;
    newsFeed: string;
    noReportsYet: string;
    trackingNow: string;
    reportTimelineHint: string;
    subscriptionTopic: string;
    todayPush: string;
  };

  // Live Run Feedback
  liveRunFeedback: {
    title: string;
    phaseUnderstand: string;
    phaseExecute: string;
    phaseVerify: string;
    generatingActionDraft: string;
    generatingReasoning: string;
    iteration: (n: number) => string;
    contentPreview: string;
    updatingTodos: string;
    writingFile: string;
    writeComplete: string;
    readingFile: string;
    readingContext: string;
    runningCommand: string;
    calling: string;
  };

  // Public Thinking Status
  publicThinkingStatus: {
    waitingForModel: string;
    firstResponseSlow: string;
    modelWorking: string;
    thinkingCompleted: string;
    slowResponse: string;
    reconnecting: string;
    processing: string;
    ttftLabel: string;
    ttftHint: string;
  };

  // Evolution Dashboard
  evolutionDashboard: {
    title: string;
    pageDescription: string;
    reflexRules: string;
    showRuntimeMonitor: string;
    hideRuntimeMonitor: string;
    runtimeMonitorDescription: string;
    skills: string;
    memories: string;
    knowledgeGraph: string;
    improvementScore: string;
    proactive: string;
    autoExtracted: string;
    totalEntries: string;
    entities: string;
    relationships: string;
    communities: string;
    learningEvents: string;
    reports: string;
    skillsAutoCreated: string;
    // Skill performance table
    skillPerformance: string;
    all: string;
    autoExtractedFilter: string;
    manual: string;
    declining: string;
    skillName: string;
    usageCount: string;
    successRate: string;
    avgDuration: string;
    trend: string;
    noSkillData: string;
    // Learning curve
    learningCurve: string;
    successRatePct: string;
    avgDurationSec: string;
    skillsUsed: string;
    // Memory growth
    memoryGrowth: string;
    facts: string;
    preferences: string;
    learnedSkills: string;
    // Timeline
    extractionTimeline: string;
    autoLabel: string;
    manualLabel: string;
    // Recommendations
    recommendations: string;
    noRecommendations: string;
    // Sync from Intel
    syncFromIntel: string;
    syncing: string;
    syncComplete: string;
    syncFailed: string;
    lastSync: string;
    // Errors
    connectionFailed: string;
    loading: string;
    refresh: string;
    retryLoading: string;
    selfImprovement: string;
    measureChanges: string;
    noLearningData: string;
    noMemoryData: string;
    noSkillExtractionEvents: string;
    continuousLearningDesc: string;
    viewInTaskBoard: string;
    active: string;
    disabled: string;
    of100: string;
    // Growth story hero
    recentEvolutionTitle: string;
    growthSummary: (
      totalMemories: number,
      totalSkills: number,
      learningEvents: number,
    ) => string;
    noEvidenceDescription: string;
    overallImprovementLabel: string;
    observeTasks: string;
    observeTasksDescription: string;
    accumulateMemories: string;
    accumulateMemoriesDescription: string;
    formSkills: string;
    formSkillsDescription: string;
    proposeImprovements: string;
    proposeImprovementsDescription: string;
    unitTimes: string;
    unitItems: string;
    unitSkills: string;
    unitSuggestions: string;
    autoExtractedSkills: string;
    autoExtractedSkillsShare: (percent: string) => string;
    waitingForSkillAccumulation: string;
    reusableMemoryLibrary: string;
    ruleMemoryCount: (count: number) => string;
    memoryDetailDefault: string;
    nextSteps: string;
    nextStepsAvailable: string;
    nextStepsNone: string;
    // Learning story
    capabilityTrend: string;
    noTrendYet: string;
    recentChange: string;
    currentSuccessRate: string;
    recentSkillCalls: string;
    // Skill story
    strongerSkills: string;
    noSkillPerformanceYet: string;
    skillCalls: (count: number) => string;
    // Recommendations story
    howToImproveNext: string;
    noPendingRecommendations: string;
    storyNoRealChangeTitle: string;
    storyRealChangeTitle: (count: number) => string;
    storyNoRealChangeDescription: (count: number) => string;
    storyRealChangeDescription: (count: number) => string;
    notEvolutionBadge: string;
    observedTasks: string;
    observedTasksPlainDescription: string;
    savedLessons: string;
    savedLessonsPlainDescription: string;
    changedBehaviors: string;
    changedBehaviorsPlainDescription: string;
    actualChangesTitle: string;
    actualChangesEmptyTitle: string;
    actualChangesEmptyDescription: string;
    changeRuleLabel: string;
    changeMemoryLabel: string;
    changeSkillLabel: string;
    ruleFutureEffect: string;
    memoryFutureEffect: string;
    skillFutureEffect: string;
    observationsTitle: string;
    observationsDescription: string;
    unnamedObservedTask: string;
    taskCompleted: string;
    taskNotCompleted: string;
    taskSteps: (count: number) => string;
    nextActionTitle: string;
    reflectionActionTitle: (count: number) => string;
    reflectionActionDescription: string;
    technicalDetails: string;
    metricsNotEvolutionNote: string;
  };

  // Wiki Panel
  wiki: {
    title: string;
    repoWiki: string;
    docs: string;
    static: string;
    generate: string;
    generateWiki: string;
    generatingWiki: string;
    generateConfirmTitle: string;
    generateConfirmBody: string;
    generateStarted: string;
    generateComplete: string;
    generateFailed: string;
    update: string;
    updateConfirmTitle: string;
    updateConfirmBody: string;
    updateUpToDate: string;
    updatedFiles: (count: number) => string;
    updateFailed: string;
    loadingWiki: string;
    noWikiYet: string;
    noWikiDesc: string;
    backendUnavailable: string;
    refresh: string;
    expand: string;
    collapse: string;
    editing: string;
    save: string;
    editDocument: string;
    documentSaved: string;
    documentSaveFailed: string;
    selectDocument: string;
    failedToLoad: string;
    files: string;
    modules: string;
    overview: string;
    outdated: string;
    filesChanged: (count: number) => string;
    steps: string;
    elapsed: string;
    warnings: (count: number) => string;
    autosyncOnLabel: string;
    autosyncOffLabel: string;
    autosyncOnHint: string;
    autosyncOffHint: string;
    autosyncOn: string;
    autosyncOff: string;
    autosyncSaveFailed: string;
    autosyncSavedNotWatching: string;
  };

  // Onboarding
  onboarding: {
    title: string;
    welcomeToEcho: string;
    yourAIPlatform: string;
    welcomeDesc: string;
    chatModes: string;
    chatModesDesc: string;
    modeChat: string;
    modeChatDesc: string;
    modeCode: string;
    modeCodeDesc: string;
    modeTeam: string;
    modeTeamDesc: string;
    keyFeatures: string;
    keyFeaturesDesc: string;
    featureAgentWorld: string;
    featureAgentWorldDesc: string;
    featureWorkflows: string;
    featureWorkflowsDesc: string;
    featureSkillsMarket: string;
    featureSkillsMarketDesc: string;
    featureTaskBoard: string;
    featureTaskBoardDesc: string;
    quickTips: string;
    quickTipsDesc: string;
    tipSearch: string;
    tipToggleSidebar: string;
    tipSlashCommands: string;
    tipMentionAgents: string;
    skip: string;
    previous: string;
    next: string;
    getStarted: string;
    goToStep: (step: number) => string;
  };

  // A2A Agents
  a2a: {
    title: string;
    refresh: string;
    registerAgent: string;
    remoteAgentUrl: string;
    connect: string;
    remove: string;
    testConnection: string;
    connectionSuccessful: string;
    connectionFailed: string;
    noAgents: string;
    noAgentsDesc: string;
    endpoint: string;
    description: string;
    capabilities: string;
    skills: string;
    streaming: string;
    multiTurn: string;
    push: string;
    sendTask: string;
    sendTaskPlaceholder: string;
    send: string;
    artifacts: string;
    status: string;
    nonTextContent: string;
    binaryContent: string;
  };

  // Live Tool Timeline
  liveTools: {
    terminal: string;
    writeFile: string;
    editFile: string;
    readFile: string;
    searchFiles: string;
    searchContent: string;
    webSearch: string;
    gitStatus: string;
    gitCommit: string;
    gitDiff: string;
    streamRecovery: string;
    running: string;
    genericAction: string;
  };

  // Live Tool Timeline detail labels
  liveToolTimeline: {
    searchingWeb: string;
    searchingQuery: (query: string) => string;
    searchedPages: (count?: number) => string;
    searchResultCovering: (query: string) => string;
    searchResultInContext: string;
    browsingPage: string;
    browsedOnePage: string;
    sourceFrom: (source: string) => string;
    pageOpenedAndExtracted: string;
    parallelDispatching: (count?: number) => string;
    parallelDispatchFailed: (count?: number) => string;
    parallelTasksReturned: (count?: number) => string;
    rolesWithNextStep: (roles: string) => string;
    subtaskAggregation: string;
    callSubAgent: (role: string) => string;
    callSubAgentShort: string;
    focusedDelegation: string;
    writeBlackboard: (key: string) => string;
    writeBlackboardShort: string;
    saveBlackboardFinding: string;
    readBlackboardDirectory: string;
    readBlackboard: (key: string) => string;
    readBlackboardShort: string;
    pullParallelResults: string;
    thoughtDetailLabel: (iteration?: number) => string;
    modelPublicReasoningFragment: string;
    modelPublicReasoningStream: string;
    modelOutputtingReasoning: string;
    invokeSkillProcess: string;
    understandTask: string;
    readingUserRequirements: string;
    connectRuntime: string;
    establishingCallbackChannel: string;
    renderingModelOutput: string;
    incrementalTextReceived: string;
    thinking: string;
    modelOrganizingNextStep: string;
    modelOrganizingNextStepWithWait: (seconds: number) => string;
    modelOutputIncomplete: string;
    providerRejected: string;
    modelOutputReceived: string;
    modelStartedReturning: string;
    readFileToUnderstand: string;
    viewDirectoryStructure: string;
    writeFileContent: string;
    scopePath: (path: string) => string;
    matchPattern: (pattern: string) => string;
    keyPagesExtracted: string;
    searchCovering: (queries: string) => string;
    searchResultCalibrate: string;
    searchingWebRounds: (rounds: number) => string;
    browsingPageCount: (count: number) => string;
    browsedPagesCount: (count: number) => string;
    searchRoundIndex: (round: number) => string;
    pageItemIndex: (page: number) => string;
    collapseSearchDetails: string;
    expandSearchDetails: string;
    collapseToolDetails: string;
    expandToolDetails: string;
    collectingEvidence: string;
    searchRoundFailed: string;
    marketSizeLeads: string;
    competitionLeads: string;
    technologyLeads: string;
    demandLeads: string;
    roundResultsRead: string;
    detailTitles: {
      input: string;
      thought: string;
      publicReasoning: string;
      result: string;
      observation: string;
      preview: string;
    };
    statusRunning: string;
    statusDone: string;
    statusFailed: string;
    statusWaitingApproval: string;
    showMoreResults: (count: number) => string;
    collapseResults: string;
    applyingSkill: (running: boolean) => string;
    planningNextStep: (running: boolean) => string;
    readingFile: (running: boolean) => string;
    browsingDirectory: (running: boolean) => string;
    searchingFiles: (running: boolean) => string;
    searchingText: (running: boolean) => string;
    runningCommand: (running: boolean) => string;
    creatingFile: (running: boolean) => string;
    writingFile: (running: boolean) => string;
    editingFile: (running: boolean) => string;
    readingGitStatus: (running: boolean) => string;
    readingGitDiff: (running: boolean) => string;
    committingGit: (running: boolean) => string;
  };

  // Store utilities
  storeUtils: {
    appCategoryLabels: Record<string, string>;
    technicalDetails: string;
    createPluginPrompt: string;
  };

  // Local skill directory panel
  localSkillDirectory: {
    errorTitle: string;
    retryLabel: string;
    hideInternalSkills: string;
    showInternalSkills: (count: number) => string;
    verified: string;
    localCapability: string;
    marketReasonMerged: string;
    internalSkill: string;
    visibilityDuplicate: string;
    visibilityProvider: string;
    visibilitySpecialized: string;
    visibilityDeprecated: string;
    visibilityInternal: string;
    enabled: string;
    enable: string;
  };

  // Annotations
  annotations: {
    resolve: string;
    unresolve: string;
    delete: string;
    resolved: string;
    reply: string;
    replyPlaceholder: string;
    addComment: string;
    addCommentPlaceholder: string;
    comment: string;
    cancel: string;
    noAnnotations: string;
    noAnnotationsHint: string;
    annotation: (count: number) => string;
    showResolved: (count: number) => string;
    hideResolved: (count: number) => string;
    anonymous: string;
    sendReply: string;
    justNow: string;
    minutesAgo: (count: number) => string;
    hoursAgo: (count: number) => string;
    daysAgo: (count: number) => string;
  };

  // Mention Autocomplete
  mentions: {
    mentions: string;
    searching: string;
    noResults: string;
    navigate: string;
    select: string;
    close: string;
  };

  // Follow-up Suggestions
  followups: {
    explainCode: string;
    howToFix: string;
    writeTests: string;
    summarize: string;
    tellMore: string;
  };

  // Code Editor
  codeEditor: {
    modified: string;
    reset: string;
    save: string;
    saving: string;
    saved: string;
    fileSaved: string;
    fileSaveFailed: string;
    diagnose: string;
    diagnosticsClean: string;
    diagnosticsIssues: string;
    diagnosticsFailed: string;
    goToDefinitionTitle: (symbol: string) => string;
    findReferencesTitle: (symbol: string) => string;
    definitionButton: string;
    referencesButton: string;
    definitionFound: (symbol: string, file: string, line: number) => string;
    definitionNotFound: (symbol: string) => string;
    definitionLookupFailed: string;
    referencesFound: (count: number, symbol: string) => string;
    referencesNotFound: (symbol: string) => string;
    referencesLookupFailed: string;
  };

  // Cron Settings
  cronSettings: {
    title: string;
    description: string;
    noTasks: string;
    last: string;
    jobName: string;
    jobNamePlaceholder: string;
    commandToRun: string;
    commandPlaceholder: string;
    cronExpression: string;
    cronPlaceholder: string;
    cronHint: string;
    create: string;
    cancel: string;
    addTask: string;
    // Additional fields
    loadFailed: string;
    createSuccess: string;
    createFailed: string;
    deleteSuccess: string;
    deleteFailed: string;
    needsAuth: string;
    nameRequired: string;
    commandRequired: string;
    cronRequired: string;
    cronInvalid: string;
    deleteConfirmTitle: string;
    deleteConfirmDescription: (name: string) => string;
    deleteTask: (name: string) => string;
  };

  // Team Input
  teamInput: {
    placeholder: string;
    assigneeAll: string;
    assigneeCount: (count: number) => string;
    assigneeHint: string;
    assigneeMenuTitle: string;
    clearAssignee: string;
    localFileAgent: string;
    localFileAgentHint: string;
  };

  // Mobile
  mobile: {
    micDisabledAria: string;
  };

  fileTree: {
    emptyDirectory: string;
    openFolderAria: (name: string) => string;
    openFileAria: (name: string) => string;
  };

  // TAOR Indicator
  taor: {
    think: string;
    thinking: string;
    act: string;
    acting: string;
    observe: string;
    observing: string;
    repeat: string;
    iteration: string;
  };

  // Bundle Info
  bundleInfo: {
    title: string;
    appVersion: string;
    license: string;
    environment: string;
    vite: string;
    react: string;
  };

  // Model Picker
  modelPicker: {
    selectModel: string;
    tabOfficial: string;
    tabCustom: string;
    addModel: string;
    noCustomModels: string;
    officialDisabled: string;
    loginRequired: string;
    enabling: string;
    clickToEnable: string;
    recommended: string;
    bindAccountFirst: string;
    bindAccountDesc: string;
    modelEnabled: (name: string) => string;
    enableFailed: string;
    enableFailedWithMessage: (msg: string) => string;
    autoModelLabel: string;
    autoModelDescription: string;
    /** Compact badge for the picker row (e.g. "智能" / "Smart"). */
    autoModelBadge: string;
    longContextHint: string;
    contextLength: string;
    contextStandard: string;
    contextMax: string;
  };

  // Account Settings
  accountSettings: {
    creditsBalance: string;
    octAccount: string;
    available: string;
    refreshing: string;
    refresh: string;
    byType: string;
    granted: string;
    expiredOrFrozen: string;
    expired: string;
    creditsDescription: string;
    creditsRefreshed: string;
    refreshFailed: string;
    primaryAccount: string;
    linkGoogle: string;
    linkGithub: string;
    thirdPartyLinkUnavailable: string;
    unlinkConfirm: string;
    systemManaged: string;
    clickToChangeAvatar: string;
    deleteAccountConfirm: string;
    deleteAccountWarning: string;
    typeToConfirm: string;
    confirmDelete: string;
    factoryResetTitle: string;
    factoryResetDescription: string;
    factoryResetDialogDescription: string;
    factoryResetTypeToConfirm: string;
    factoryResetTypeMismatch: string;
    factoryResetSuccess: string;
    factoryResetFailed: string;
    factoryResetPending: string;
    factoryResetConfirm: string;
    // Avatar / account session hints
    avatarTooLarge: string;
    sessionExpired: (reason: string) => string;
    sessionExpiredDefaultReason: string;
    sessionCacheHint: string;
    cachedSuffix: string;
    expiredTooltip: string;
    profileUpdated: string;
    avatarUploaded: string;
    accountUnlinked: string;
    privacyUpdated: string;
    dataUnavailable: string;
    retry: string;
  };

  // Subscription Settings
  subscriptionSettings: {
    upgradeTitle: string;
    upgradeDesc: string;
    currentPlan: string;
    upgradeNow: string;
    contactUs: string;
    supportEmail: string;
    invoiceHint: string;
    totalCredits: (total: string) => string;
    billingUnavailableTitle: string;
    billingUnavailableDescription: string;
    subscriptionUnavailable: string;
    plansUnavailable: string;
    noPlans: string;
    reloadSubscription: string;
    reloadBilling: string;
    reloadPlans: string;
    cancelTitle: string;
    cancelDescription: string;
    keepPlan: string;
    confirmCancel: string;
    cancelled: string;
    plans: {
      plus: {
        name: string;
        credits: string;
        badge: string | null;
        features: string[];
      };
      pro: {
        name: string;
        credits: string;
        badge: string | null;
        features: string[];
      };
      max: {
        name: string;
        credits: string;
        badge: string | null;
        features: string[];
      };
    };
  };

  // Swarm Panel
  swarmPanel: {
    title: string;
    collapse: string;
    noActiveTasks: string;
    emptyHint: string;
    viewing: string;
    backToLatest: string;
    taskWaiting: string;
    taskRunning: string;
    taskNoSteps: string;
    noSteps: string;
    parallelTasks: string;
    status: {
      completed: string;
      failed: string;
      pending: string;
    };
    statusLabels: string[];
    taskStatuses: {
      pending: string;
      running: string;
      completed: string;
      failed: string;
    };
    // SwarmPanel (workspace task-monitor) extras
    panelStatusPending: string;
    panelStatusRunning: string;
    panelStatusCompleted: string;
    panelStatusFailed: string;
    statTotal: string;
    statCapacity: string;
    statRunning: string;
    statCompleted: string;
    statFailed: string;
    statEvidence: string;
    phaseDispatch: string;
    phaseExecute: string;
    phaseAggregate: string;
    phaseSynthesize: string;
    deliveryReady: string;
    deliveryNeedsReview: string;
    deliveryPrimary: string;
    deliverySupporting: (n: number) => string;
    deliveryRetry: (n: number) => string;
    deliverySummary: string;
    deliveryCopy: string;
    deliveryCopied: string;
    deliveryReplayExport: string;
    deliveryReplayExported: string;
    deliveryRetryNote: (n: number) => string;
    deliveryCoverage: (answered: number, total: number) => string;
    deliveryNext: string;
    deliveryActionUsePrimary: string;
    deliveryActionUsePrimaryAndRetry: string;
    deliveryActionAskMembers: string;
    deliveryActionRetryOrFallback: string;
    deliveryActionFallback: string;
    rhythmActive: (name: string) => string;
    rhythmWorking: string;
    rhythmDelivered: string;
    rhythmNeedsReview: string;
    rhythmProgress: (done: number, total: number) => string;
    rhythmEvidence: (n: number) => string;
    rhythmResults: (n: number) => string;
    taskListHeader: string;
    noSwarmTasksTitle: string;
    noSwarmTasksHint: string;
    loadFailed: string;
    taskComplete: string;
    replayTeamWork: string;
    noActivity: string;
  };

  // Subtask
  subtask: {
    cancelled: string;
    timedOut: string;
  };

  // Dispatch Composer
  dispatchComposer: {
    title: string;
    connectedBatch: (id: string) => string;
    placeholder: string;
    concurrency: string;
    autoSplit: string;
    model: string;
    splitting: string;
    dispatching: string;
    processing: string;
    dispatchButton: string;
    dispatchFailed: string;
  };

  // Credits
  credits: {
    remaining: (n: number) => string;
    remainingWithHint: (n: number) => string;
    credits: string;
    refreshed: string;
    refreshFailed: string;
  };

  // Community credits ledger (本地积分账本 / 积分中心)
  creditsCenter: {
    title: string;
    totalBalance: string;
    accountBalance: string;
    communityBalance: string;
    signIn: string;
    signInDone: string;
    signInSuccess: (n: number) => string;
    signInFailed: string;
    earned: string;
    spent: string;
    ledger: string;
    emptyLedger: string;
    earnHints: string;
    earnHintSignIn: (n: number) => string;
    earnHintPublish: (n: number) => string;
    earnHintFork: (n: number) => string;
    earnHintLike: (n: number) => string;
    spendNoBalance: string;
  };

  // Daily credits claim
  dailyClaim: {
    title: string;
    description: (fixed: number) => string;
    drawButton: string;
    drawHint: (max: number) => string;
    directClaim: (fixed: number) => string;
    claimedToday: string;
    claiming: string;
    drawing: string;
    claimSuccess: (n: number) => string;
    claimFailed: string;
    dismissToday: string;
    manualEntry: string;
  };

  // Implementation note.
  payOrder: {
    title: string;
    subtitle: string;
    backToPricing: string;
    purchaseInfo: string;
    planName: string;
    amount: string;
    confirmPayment: string;
    polling: string;
    paidSuccess: string;
    pollingTimeout: string;
    notLinked: string;
    notLinkedDesc: string;
    qrExpired: string;
    loadingGoods: string;
    goodsFailed: string;
    recommended: string;
    perMonth: string;
    perYear: string;
    oneTime: string;
    subscribeNow: string;
    priceTag: (yuan: string, unit: string) => string;
  };

  // Auth
  // Login page (pages/Login.tsx)
  loginPage: {
    appSubtitle: string;
    checkingProviders: string;
    noProvidersTitle: string;
    noProvidersHint: string;
    tabPhone: string;
    tabLocal: string;
    termsPrefix: string;
    termsLink: string;
    jwtNote: string;
    mockModeBanner: string;
    mockCodeLabel: (code: string) => string;
    mockServerLog: string;
    phoneLabel: string;
    sending: string;
    resendInSec: (n: number) => string;
    getCode: string;
    sentToPrefix: string;
    resend: string;
    codeLabel: string;
    backButton: string;
    verifying: string;
    loginButton: string;
    spinnerWait: string;
    localBanner: string;
    usernameLabel: string;
    displayNameLabel: string;
    loggingIn: string;
    errorServiceDisabled: string;
    errorUpstream: string;
    errorCodeInvalid: string;
    errorNotInWhitelist: string;
  };

  // Register page (app/register/page.tsx)
  registerPage: {
    loadingText: string;
    badgeText: string;
    heroTitleLine1: string;
    heroTitleLine2: string;
    heroDescription: string;
    cardTitle: string;
    cardDescription: string;
    usernameLabel: string;
    usernamePlaceholder: string;
    emailLabel: string;
    passwordLabel: string;
    passwordPlaceholder: string;
    confirmPasswordLabel: string;
    confirmPasswordPlaceholder: string;
    submitting: string;
    submitButton: string;
    alreadyHaveAccount: string;
    loginLink: string;
    toastFillRequired: string;
    toastPasswordMismatch: string;
    toastUsernameTooShort: string;
    toastPasswordTooShort: string;
    toastSuccess: string;
    toastFailed: string;
  };

  // App Authorization page
  appAuth: {
    pageTitle: string;
    pageSubtitle: string;
    searchPlaceholder: string;
    connectedCount: (n: number) => string;
    tabAll: string;
    connectedSectionHeader: (n: number) => string;
    availableSectionHeader: (n: number) => string;
    loadingText: string;
    noAvailableApps: string;
    statusConnected: string;
    statusExpired: string;
    statusRevoked: string;
    statusError: string;
    statusPending: string;
    typePrefix: string;
    connectedAtPrefix: string;
    lastUsedAtPrefix: string;
    testConnectionTooltip: string;
    refreshTooltip: string;
    disconnectTooltip: string;
    connectButton: string;
    connectDialogTitle: (name: string) => string;
    apiKeyLabelFallback: string;
    tokenLabelFallback: string;
    cookieLabel: string;
    howToGetApiKey: string;
    cookieHint: (domain: string) => string;
    oauthRedirectHint: (name: string) => string;
    dialogCancel: string;
    browserDialogDescription: string;
    browserStatusSuccess: string;
    browserStatusFailed: string;
    browserStatusCancelled: string;
    browserStatusWaiting: string;
    browserStep1: string;
    browserStep2: string;
    browserStep3: string;
    browserCookiesLabel: string;
    browserBearerLabel: string;
    browserCaptured: string;
    browserNotCaptured: string;
    browserCloseButton: string;
    browserCancelButton: string;
    confirmDisconnect: string;
    // Toasts fired from core/integrations/hooks.ts
    toastAuthCreated: string;
    toastAuthDeleted: string;
    toastAuthRefreshed: string;
    toastBrowserAuthSuccess: string;
    toastBrowserAuthFailed: string;
    toastBrowserAuthCancelled: string;
  };

  // FileActivityIndicator + PreviewRefreshIndicator (observability chrome)
  activityIndicators: {
    recentFileActivity: (n: number) => string;
    fileActivityTitle: (n: number) => string;
    realtimeLabel: string;
    filesCount: (n: number) => string;
    previewLastRefresh: (reason: string) => string;
    previewWaitingForRefresh: string;
    previewPrefix: (count: number) => string;
  };

  changesPanel: {
    title: string;
    empty: string;
    emptyHint: string;
  };

  codeWelcome: {
    title: string;
    fixBug: string;
    fixBugPrompt: string;
    addFeature: string;
    addFeaturePrompt: string;
    refactor: string;
    refactorPrompt: string;
    writeTests: string;
    writeTestsPrompt: string;
    explainCode: string;
    explainCodePrompt: string;
    optimize: string;
    optimizePrompt: string;
    hint: string;
  };

  workingSet: {
    title: string;
    empty: string;
    editing: string;
    reading: string;
    understand: string;
    execute: string;
    verify: string;
  };

  contextWindow: {
    title: string;
    system: string;
    tools: string;
    memory: string;
    history: string;
  };

  contextCompressor: {
    compressing: string;
    full: string;
    clickToCompress: string;
    compressContext: string;
    contextUsage: string;
    tokens: string;
    threshold: string;
    contextFull: string;
    autoCompressed: string;
    title: string;
    description: string;
    tip: string;
  };

  // Reflex page (app/workspace/reflex/page.tsx)
  reflexPage: {
    subtitle: string;
    lastRefreshPrefix: (time: string) => string;
    pageTitle: string;
    editRulesButton: string;
    reloadButton: string;
    reloadResetButton: string;
    reloadingStatus: string;
    reloadLoaded: (rules: number, statsReset: boolean) => string;
    reloadError: (error: string) => string;
    fetchFailed: string;
    dataLoading: string;
    dataUnavailable: string;
    dataRefreshFailed: string;
    retryButton: string;
    reloadFailed: string;
    statTry: string;
    statHit: string;
    statHitRate: string;
    statRules: string;
    statStale: string;
    statLastHourHits: string;
    sparklineTitle: string;
    sparklineEmpty: string;
    sparklineUnavailable: string;
    rulesTableTitle: string;
    responseTiersTitle: string;
    colRule: string;
    colKind: string;
    colPatternType: string;
    colPrio: string;
    colTries: string;
    colHits: string;
    colRate: string;
    colLast: string;
    noRulesLoaded: string;
    rulesUnavailable: string;
    tierEnabled: string;
    tierDisabled: string;
    tierSize: string;
    tierHits: string;
    tierMisses: string;
    tierRate: string;
    tierEndpoint: string;
    tierSimilarity: string;
    badgeAB: (count: number) => string;
    badgeGated: string;
    badgeStale: string;
    badgeUnexercised: string;
    perActor: string;
    minutesAgo: (m: number) => string;
    hoursAgo: (h: number) => string;
    daysAgo: (d: number) => string;
  };

  // Reflex YAML editor page (app/workspace/reflex/edit/page.tsx)
  reflexEditor: {
    backButton: string;
    pageTitle: string;
    mtimePrefix: (time: string) => string;
    reloadFromDisk: string;
    runTestsButton: string;
    saveAndReload: string;
    saveNoReload: string;
    keyboardHintSuffix: string;
    testResultsCard: string;
    errorPrefix: (msg: string) => string;
    testSummary: (passed: number, total: number, failed: number) => string;
    testFailureRow: (ruleId: string, input: string, reason: string) => string;
    loadingEditor: string;
    statusIdle: string;
    statusLoading: string;
    statusSaving: string;
    statusLoaded: string;
    statusSaved: (rules: number) => string;
    statusReloaded: (active: number) => string;
    statusReloadFailed: (err: string) => string;
    statusLoadFailed: (err: string) => string;
    statusSaveFailed: (err: string) => string;
    statusRunningTests: string;
    statusTestError: (err: string) => string;
    statusFetchError: string;
    statusSaveError: string;
    statusTestErrorFallback: string;
    statusUnknown: string;
    modeCard: string;
    modeYaml: string;
    cardEmpty: string;
    cardAddNew: string;
    cardField_id: string;
    cardField_trigger: string;
    cardField_reply: string;
    cardField_priority: string;
    triggerMode_exact: string;
    triggerMode_contains: string;
    triggerMode_regex: string;
    priority_low: string;
    priority_medium: string;
    priority_high: string;
    cardAdvancedBadge: string;
    cardDelete: string;
    cardConfirmDelete: string;
    cardSavedHint: string;
    cardField_action: string;
    cardField_replyOnFailure: string;
    cardField_replyOnFailurePlaceholder: string;
    cardField_hubPreset: string;
    cardField_headers: string;
    cardField_body: string;
    cardField_addHeader: string;
    actionMode_none: string;
    actionMode_webhook: string;
    actionMode_mqtt: string;
    cardField_replySource: string;
    replySource_text: string;
    replySource_workflow: string;
    replySource_slowHint: string;
    cardField_workflowPick: string;
    cardField_workflowFallback: string;
  };

  // RecipeForge panel (app/workspace/reflex/gepa-panel.tsx) · prompt evolution
  recipeForge: {
    panelTitle: string;
    reflectionPathBadge: string;
    addendumAppliedTitle: string;
    addendumLive: string;
    addendumNone: string;
    stateLoading: string;
    stateUnavailable: string;
    addendumUnavailable: string;
    addendumClearButton: string;
    addendumBytes: (size: number) => string;
    knobIterations: string;
    knobEvalTasks: string;
    autoProposeButton: string;
    autoProposeRunning: string;
    autoProposeTitle: string;
    runForgeButton: string;
    runForgeRunning: string;
    paretoFrontTitle: (count: number) => string;
    iterCount: (count: number) => string;
    elapsedSeconds: (seconds: number) => string;
    thisRunHistory: (count: number) => string;
    addendumsByScope: (count: number) => string;
    addendumCsvTooltip: string;
    addendumRefresh: string;
    addendumGlobalHint: string;
    canaryTitle: string;
    canaryRefresh: string;
    canaryEmpty: string;
    canaryUnavailable: string;
    canaryCountsUnavailable: string;
    canaryCounts: (active: number, rolledBack: number, total: number) => string;
    canaryPhase: (phase: string) => string;
    canaryRate: (rate: number) => string;
    canarySamples: (sample: number, success: number, failure: number) => string;
    canaryCandidate: (candidateId: string) => string;
    canaryRecipe: (recipeId: string) => string;
    canaryProposal: (proposalId: string) => string;
    canaryRollbackReason: (reason: string) => string;
    proposalDetailsButton: string;
    proposalDetailsTitle: string;
    proposalDetailsLoading: string;
    proposalDetailsStatus: (status: string) => string;
    proposalDetailsCanaries: (count: number) => string;
    proposalDetailsRollbacks: (count: number) => string;
    proposalDetailsMetadata: string;
    pastRunsTitle: (count: number) => string;
    pastRunsCsvTooltip: string;
    pastRunsRefresh: string;
    applyButton: string;
    applyRecipeButton: (recipeId: string) => string;
    applyGlobalButton: string;
    cancelButton: string;
    bestBadge: string;
    promptPreview: string;
    deleteButton: string;
    confirmDeleteButton: string;
    globalScope: string;
    perRecipeScope: string;
    recipePrefix: string;
    triggerManual: string;
    triggerAutoPropose: string;
    bestAvg: (score: number) => string;
    converged: string;
    notConverged: string;
    noIterationsYet: string;
    convergenceChartLabel: string;
    bestSoFarLabel: string;
    perIterLabel: string;
    historySkipped: (iter: string, reason: string) => string;
    historyEarlyStop: (iter: string) => string;
    historySeed: (frontSize: string) => string;
    historyIter: (iter: string) => string;
    historyImproved: string;
    previewSummary: string;
    nativeEvidenceTitle: string;
    nativeEvidenceReplay: (score: number) => string;
    nativeEvidenceSandboxReplay: (score: number, passed?: boolean) => string;
    nativeEvidenceTurnReplay: (score: number, passed?: boolean) => string;
    nativeEvidenceLLMReplay: (score: number, passed?: boolean) => string;
    nativeEvidenceCases: (count: number) => string;
    nativeEvidenceMetric: (
      label: string,
      score: number | null | undefined,
    ) => string;
    nativeEvidenceWeakCase: (caseId: string, reason: string) => string;
    nativeEvidenceMissing: (signals: string) => string;
    nativeEvidenceNoWeakCases: string;
    statusFetchFailed: string;
    statusRunFailed: string;
    statusRunInProgress: (nIter: number, nSeconds: number) => string;
    statusApplyFailed: (error: string) => string;
    statusDeleteFailed: (error: string) => string;
    statusDeleteFailedGeneric: string;
    statusRunError: (error: string) => string;
    statusNoRun: (reason: string) => string;
    statusRunSuccess: (iter: string, elapsed: string, front: number) => string;
    statusAutoProposeInProgress: (nSeconds: number) => string;
    statusAutoProposeError: (error: string) => string;
    statusNoPropose: string;
    statusProposeSkipped: (reason: string) => string;
    statusProposeSuccess: (count: number) => string;
    statusApplying: (candidateId: string, where: string) => string;
    statusApplied: (scope: string, size: number, path: string) => string;
    statusDeleteAddendum: string;
    statusDeleted: (path: string) => string;
    statusNothingToDelete: string;
    clearAddendumPath: (path: string) => string;
  };

  // AppAuth wrapper page (app/workspace/app-auth/page.tsx)
  appAuthWrapperPage: {
    securityKicker: string;
    pageTitle: string;
    pageSubtitle: string;
    feature1Title: string;
    feature1Desc: string;
    feature2Title: string;
    feature2Desc: string;
  };

  // Observability page (app/observability/page.tsx)
  observabilityPage: {
    pageTitle: string;
    pageSubtitle: string;
    loadError: string;
    unknownStatus: string;
    statHealth: string;
    statRunningTasks: string;
    statJournalEvents: string;
    statCapabilities: string;
    cardActiveTasks: string;
    cardJournalTail: string;
    cardReflectionSnapshot: string;
    defaultStrategy: string;
    journalRow: (task: string, arm: string) => string;
    subtitle: string;
    shell: {
      startTask: string;
      runReviewTitle: string;
      runReviewDescription: string;
      liveEventsTitle: string;
      liveEventsDescription: string;
      resourcesTitle: string;
      resourcesDescription: string;
      systemTitle: string;
      systemDescription: string;
      overviewTab: string;
      eventsTab: string;
      resourcesTab: string;
      systemTab: string;
      overviewTitle: string;
      overviewDescription: string;
      openNewTask: string;
      eventsEyebrow: string;
      eventsTitle: string;
      eventsDescription: string;
      resourcesEyebrow: string;
      resourcesGroupTitle: string;
      resourcesGroupDescription: string;
      systemEyebrow: string;
      systemGroupTitle: string;
      systemGroupDescription: string;
    };
    toolEffects: {
      title: string;
      description: string;
      retryAuthorizedSuccess: string;
      retryAuthorizationFailed: string;
      pendingReview: (count: number) => string;
      noPendingReview: string;
      refreshAriaLabel: string;
      backendLabel: string;
      sharedAcrossHosts: string;
      localCoordination: string;
      committedLabel: string;
      runningLabel: string;
      loadFailed: (error: string) => string;
      empty: string;
      unknownTool: string;
      receiptMeta: (task: string, step: number, token: number) => string;
      reviewAndRetry: string;
      collapsedHistory: string;
      confirmTitle: string;
      confirmDescription: string;
      reasonPlaceholder: string;
      cancel: string;
      submitting: string;
      confirmRetry: string;
      states: {
        claimed: string;
        started: string;
        committed: string;
        indeterminate: string;
        retryAuthorized: string;
      };
    };
    swarmCardTitle: string;
    noConcurrentTasks: string;
    noConcurrentTasksHint: string;
    nestedSseNote: string;
    journalEventStream: string;
    // Tab names
    tabRuns: string;
    tabSwarm: string;
    tabBlackboard: string;
    tabJournal: string;
    tabRegeneration: string;
    tabHemolymph: string;
    tabCost: string;
    // Connection status
    connected: string;
    idle: string;
    // Swarm panel
    stepsCount: (n: number) => string;
    // Blackboard panel
    activeTurns: string;
    noActiveBlackboard: string;
    noActiveBlackboardHint: string;
    snapshot: string;
    selectTurnHint: string;
    selectTurnHintDesc: string;
    emptyBlackboard: string;
    keyCount: string;
    keysLabel: string;
    taskPrefix: string;
    // Journal panel
    pause: string;
    resume: string;
    clear: string;
    clearConfirmTitle: string;
    clearConfirmDescription: string;
    noEvents: string;
    noEventsHint: string;
    eventActionFile: string;
    eventArtifact: string;
    eventArtifactScreenshot: string;
    eventSuccess: string;
    eventFailure: string;
    eventDurationSuffix: (ms: number) => string;
    // Run review panel
    runReviewTitle: string;
    runReviewHint: string;
    runReviewRuns: string;
    runReviewRunning: string;
    runReviewErrors: string;
    runReviewTokens: string;
    runReviewCost: string;
    runReviewEmpty: string;
    runReviewEmptyHint: string;
    runReviewStatusRunning: string;
    runReviewStatusDone: string;
    runReviewStatusError: string;
    runReviewEvents: (n: number) => string;
    runReviewCostLine: (tokens: number, usd: number) => string;
    runReviewToolSummary: string;
    runReviewFiles: string;
    runReviewLearningSignals: string;
    runReviewTaskPrefix: string;
    runReviewMetricForged: (count: number) => string;
    runReviewMetricRules: (count: number) => string;
    runReviewMetricMems: (count: number) => string;
    runReviewMetricTriples: (count: number) => string;
    runReviewMetricTraj: (count: number) => string;
    runReviewMetricRecipes: (count: number) => string;
    // Regeneration panel
    loading: string;
    errorPrefix: string;
    crossTenantAdminRequired: string;
    trajectoryTotal: string;
    failureCount: string;
    regenProducers: {
      skillForge: string;
      skillForgeHint: string;
      ruleExtractor: string;
      ruleExtractorHint: (n: number) => string;
      memoryConsolidator: string;
      memoryConsolidatorHint: (n: number) => string;
      kgUpdater: string;
      kgUpdaterHint: string;
      workflowRewriter: string;
      workflowRewriterHint: string;
      recipeEvaluator: string;
      recipeEvaluatorHint: string;
    };
    // Hemolymph panel
    latestCompose: string;
    noComposeRecords: string;
    noComposeRecordsHint: string;
    total: string;
    historyPrefix: string;
    hemolymphTable: {
      ts: string;
      usedBudget: string;
      util: string;
      recipe: string;
    };
    hemolymphBuckets: {
      system: string;
      suckers: string;
      memory: string;
      history: string;
    };
    // Cost panel
    cumulativeTokens: string;
    cumulativeUsd: string;
    commitCount: string;
    taskCount: string;
    taskGroupingPrefix: string;
    noBudgetCommits: string;
    noBudgetCommitsHint: string;
    costTable: {
      task: string;
      tokens: string;
      usd: string;
      commits: string;
      last: string;
    };
    // Status dot
    statusReady: string;
    statusWarming: string;
    statusIdle: string;
    utilizationLabel: string;
    snapshotTokensUnit: string;
  };

  // Evolution Control Panel (8-section operator console)
  evolutionControl: {
    panelTitle: string;
    refreshAriaLabel: string;
    loadingText: string;
    sections: {
      budget: string;
      skillProposals: string;
      models: string;
      mcp: string;
      curriculum: string;
      frameworks: string;
      drift: string;
      dispatch: string;
    };
    budget: {
      cardTitle: string;
      empty: string;
      consecutiveFailures: (n: number, max: number) => string;
      perHour: string;
      last24h: (ok: number, fail: number) => string;
      rejected: (budget: number, breaker: number) => string;
      dailyUsage: (used: number, limit: number) => string;
      cost: (tokens: number, usd: number) => string;
      lastReset: (value: string) => string;
      source: (source: string, events: number) => string;
      resetButton: string;
      breakerStates: {
        closed: string;
        open: string;
        halfOpen: string;
      };
      hourlyUsageAria: (
        component: string,
        used: number,
        limit: number,
      ) => string;
    };
    skillProposals: {
      cardTitle: string;
      empty: string;
      approve: string;
      reject: string;
    };
    models: {
      cardTitle: string;
      empty: string;
      runningBenchmarks: string;
      runBenchmarks: string;
      benchmarkNotes: string;
    };
    mcp: {
      cardTitle: string;
      vetAll: string;
      empty: string;
      installDisabled: string;
    };
    curriculum: {
      cardTitle: string;
      runCycle: string;
      empty: string;
      start: string;
      dismiss: string;
    };
    frameworks: {
      cardTitle: string;
      empty: string;
      baseModelPrefix: string;
      tiesPrefix: string;
      bWinRatePrefix: string;
    };
    drift: {
      cardTitle: string;
      scanButton: string;
      sweepButton: string;
      eventsHeader: string;
      repairsHeader: string;
      emptyEvents: string;
      emptyRepairs: string;
      acknowledgeButton: string;
      eventPrefix: (id: number) => string;
      diffSummary: string;
    };
    dispatch: {
      cardTitle: string;
      empty: string;
      testPrefix: (id: string) => string;
    };
  };

  // Evolution Panel (cerebrum rules/memories viewer)
  evolutionPanel: {
    title: string;
    description: string;
    summaryEmpty: string;
    summaryReady: (learned: number, total: number) => string;
    summaryHealthy: string;
    summaryFailures: (failures: number) => string;
    statusNormal: string;
    statusNeedsReview: string;
    statAvoidRule: string;
    statAvoidRuleHint: string;
    statAvoidRuleTooltip: string;
    statAvoidRuleDesc: string;
    statPatternMem: string;
    statPatternMemHint: string;
    statPatternMemTooltip: string;
    statPatternMemDesc: string;
    statAllTrajs: string;
    statAllTrajsHint: string;
    statAllTrajsTooltip: (total: number) => string;
    statAllTrajsDesc: string;
    statAllTrajsPoints: (total: number, learned: number) => string[];
    statReactLabel: string;
    statReactHint: (reviewCount: number) => string;
    statReactTooltip: (attempts: number, reviewCount: number) => string;
    statReactValue: (attempts: number, reviewCount: number) => string;
    statReactDesc: string;
    statReactPoints: (attempts: number, failures: number) => string[];
    learnedMitigationsTitle: string;
    learnedMitigationsDesc: string;
    consolidatedMemoriesTitle: string;
    consolidatedMemoriesDesc: string;
    noMitigationsHint: string;
    noMemoriesHint: string;
    linesSuffix: (n: number) => string;
    forgetLineTitle: string;
    forgetLineButton: string;
    forgetConfirmTitle: string;
    forgetConfirmDescription: string;
    nextRunImpact: string;
    failureReadBeforeWrite: string;
    failureTypeError: string;
    failureGeneric: (failure: string) => string;
    toolFailureLesson: (tool: string, failure: string, count: number) => string;
    reflectingButton: string;
    reflectButton: string;
    reflectHint: string;
    advancedTitle: string;
    reactVariantsTitle: string;
    tableName: string;
    tableSetting: string;
    tableAttempts: string;
    tableSuccessRate: string;
    variantSetting: (maxIterations: number, temperature: string) => string;
    toastReflectSkipped: (error: string) => string;
    toastReflectSuccess: (rules: number, mems: number) => string;
    toastReflectFailed: (msg: string) => string;
    toastForgetRuleSuccess: string;
    toastForgetMemorySuccess: string;
    toastDeleteFailed: (msg: string) => string;
  };

  // Evolution explain cards
  evolutionExplain: {
    fitnessTitle: string;
    noAgentSelected: string;
    loading: string;
    noFitnessData: string;
    driftTitle: string;
    noDriftData: string;
    noDriftDetected: string;
    driftDetected: (maxSeverity: string) => string;
    variantTitle: string;
    noVariantData: string;
    colName: string;
    colUsage: string;
    colSuccessRate: string;
    colStatus: string;
  };

  // Privacy settings page
  privacySettings: {
    identityLockTitle: string;
    identityLockDesc: string;
    lockedTag: string;
    unlockedTag: string;
    sourceLabel: string;
    restoreDefault: string;
    profileTitle: string;
    profileDescPrefix: string;
    profileDescDocLink: string;
    profileDescSuffix: string;
    profileStrictBlurb: string;
    profileNormalBlurb: string;
    profileLaxBlurb: string;
    activeTag: string;
    profileLoadFailed: string;
    alternativeUnlockTitle: string;
    altEnvLabel: string;
    altEnvDesc: string;
    altTurnLabel: string;
    altTurnDesc: string;
    altApiLabel: string;
    altApiDesc: string;
    toastProfileSwitched: (name: string) => string;
    toastProfileFailed: (msg: string) => string;
    toastRestoreDefault: string;
    toastLockOn: string;
    toastLockOff: string;
    toastToggleFailed: (msg: string) => string;
    // AI mode (efficiency / privacy)
    aiModeTitle: string;
    aiModeDescScanning: string;
    aiModeRecommended: (label: string) => string;
    efficiencyMode: string;
    efficiencyModeDesc: string;
    privacyMode: string;
    privacyModeDesc: string;
    detectButton: string;
    recommendedTag: string;
    enabledTag: string;
    deviceLabel: string;
    toastAiModeSwitched: (label: string) => string;
    toastAiModeSwitchFailed: (msg: string) => string;
    // Path denylist
    pathDenyTitle: string;
    pathDenyDesc: string;
    addPathButton: string;
    pathDenyEmpty: string;
    pathActionAria: (path: string) => string;
    pathDeleteButton: string;
    addPathDialogTitle: string;
    addPathDialogDesc: string;
    pathLabel: string;
    toastInvalidPath: string;
    toastPathAdded: (path: string) => string;
    toastPathAddFailed: (msg: string) => string;
    toastPathRemoved: (path: string) => string;
    toastPathRemoveFailed: (msg: string) => string;
    // LLM judge
    judgeTitle: string;
    judgeDesc: string;
    judgeUnavailable: string;
    toastJudgeEnabled: string;
    toastJudgeDisabled: string;
    toastJudgeToggleFailed: (msg: string) => string;
  };

  auth: {
    login: string;
    logout: string;
    logoutSuccess: string;
    logoutFailed: string;
    notLoggedIn: string;
    currentAccount: string;
    loginAccount: string;
    phoneNumber: string;
    emailLabel: string;
    tabEmail: string;
    verificationCode: string;
    sendCode: string;
    devCodeNotice: (code: string) => string;
    sending: string;
    loggingIn: string;
    enterDirectly: string;
    entering: string;
    guestMode: {
      title: string;
      features: string[];
    };
    errors: {
      invalidPhone: string;
      invalidEmail: string;
      sendFailed: string;
      fillRequired: string;
      emailFillRequired: string;
      emailRequired: string;
      codeRequired: string;
      invalidCode: string;
      loginFailed: string;
      enterFailed: string;
      gatewayNotEnabled: string;
    };
    success: {
      codeSent: string;
      emailCodeSent: string;
      loginSuccess: string;
      guestEntered: string;
    };
    guestUser: string;
    placeholders: {
      phone: string;
      code: string;
    };
    terms: {
      autoRegister: string;
      emailAutoRegister: string;
      agreeTo: string;
      and: string;
      userAgreement: string;
      privacyPolicy: string;
    };
    page: {
      title: string;
      subtitle: string;
      description: string;
      cardDescription: string;
      emailCardDescription: string;
    };
  };

  // Store panels (registry skills/plugins/roles)
  store: {
    skillsPanelTitle: string;
    pluginsPanelTitle: string;
    searchSkillsPlaceholder: string;
    searchPluginsPlaceholder: string;
    searchRolesPlaceholder: string;
    install: string;
    installing: string;
    installed: string;
    browseOnly: string;
    pluginsSafetyNotice: string;
    typeLabelStore: string;
    typeLabelPluginBundle: string;
    typeLabelPromptCapability: string;
    typeLabelTwinRole: string;
    categoryDigitalTwin: string;
    expertsPanelTitle: string;
    searchExpertsPlaceholder: string;
    expertTypeAgent: string;
    expertTypeTeam: string;
    typeAll: string;
    refreshTooltip: string;
    installExpertTitle: string;
    installExpertDesc: (name: string) => string;
    confirmInstall: string;
    cancelInstall: string;
    phaseDownload: string;
    phaseUnpack: string;
    phaseImport: string;
    installSuccess: (name: string) => string;
    installFailed: (name: string, reason: string) => string;
    detailTitle: (name: string) => string;
    detailProfession: string;
    detailQuickPrompts: string;
    detailTags: string;
    detailDescription: string;
    detailInstall: string;
    detailInstalled: string;
    loadMore: string;
    noMoreItems: string;
    retry: string;
    noMatchExperts: (total: number) => string;
    expertLoadingAria: string;
  };

  // Skill Categories
  skillCategories: {
    ecommerce: string;
    marketing: string;
    research: string;
    finance: string;
    documents: string;
    devtools: string;
    communication: string;
    skillManagement: string;
    systemTools: string;
    automationTools: string;
    other: string;
  };

  // Personality template selector (sidebar of agent-detail dialog etc.)
  personality: {
    templatesTitle: string;
    applySuccess: (name: string) => string;
    applyFailed: string;
    // Dicts keyed by backend template name / category slug. Missing keys
    // fall back to the backend-supplied English strings at render time.
    templateNames: Record<string, string>;
    templateDescriptions: Record<string, string>;
    categories: Record<string, string>;
  };

  // Error Boundary
  errorBoundary: {
    title: string;
    description: string;
    chunkTitle: string;
    chunkDescription: string;
    unexpectedDescription: string;
    retry: string;
    refreshPage: string;
  };

  // Hero (landing)
  hero: {
    releaseBadge: string;
    withEcho: string;
    heroDescription: string;
  };

  // Channel Pairings
  channelPairings: {
    loadFailed: string;
    retry: string;
    loading: string;
    users: string;
    groups: string;
    pending: string;
    emptyListTitle: string;
    noPendingTitle: string;
    noUsers: string;
    noGroups: string;
    noPending: string;
    copiedId: string;
    copyId: string;
    pairingDetails: string;
    autoRegisterDesc: string;
    metadataDesc: string;
  };

  // Channel Credential
  channelCredential: {
    cannotBeEmpty: string;
    connected: string;
    saveFailed: string;
    disconnected: string;
    deleteFailed: string;
    editCredential: string;
    setCredential: string;
    credentialLocalHint: string;
    howToConnect: string;
    comingSoon: string;
    unsupportedPlatform: string;
    currentConfigured: string;
    disconnect: string;
    saving: string;
    saveAndConnect: string;
    qrCodeFailed: string;
    scanConfirmed: string;
    pollFailed: string;
    wechatScanInstruction: string;
    requesting: string;
    getQrCode: string;
    qrPending: string;
    qrScanned: string;
    qrConfirmed: string;
    qrExpired: string;
    qrRejected: string;
    qrError: string;
    refreshQr: string;
    show: string;
    hide: string;
    slackBotTokenHint: string;
    slackSigningSecretHint: string;
    dingtalkWebhookUrlHint: string;
    dingtalkSecretLabel: string;
    dingtalkSecretHint: string;
    feishuAppIdHint: string;
    feishuVerificationTokenPlaceholder: string;
    feishuVerificationTokenHint: string;
    telegramBotTokenHint: string;
    telegramWebhookSecretLabel: string;
    telegramWebhookSecretPlaceholder: string;
    telegramWebhookSecretHint: string;
    discordBotTokenPlaceholder: string;
    discordBotTokenHint: string;
    discordPublicKeyPlaceholder: string;
    discordPublicKeyHint: string;
    botSuffix: string;
    confirmDisconnect: (name: string) => string;
    unsupportedPlatformDesc1: string;
    unsupportedPlatformDesc2: string;
  };

  // Execution Timeline
  executionTimeline: {
    title: string;
    loading: string;
    empty: string;
    loadFailed: string;
    noMatches: string;
    noMatchesDescription: string;
    searchPlaceholder: string;
    refresh: string;
    noTask: string;
    events: string;
  };

  // Plan Panel extras
  planPanel: {
    title: string;
    completed: string;
    inProgress: string;
    pending: string;
    steps: (completed: number, total: number) => string;
  };

  // Diagnostics Panel
  diagnosticsPanel: {
    title: string;
    noPreviewIssues: string;
    sections: {
      preview: string;
      workspace: string;
      project: string;
      thread: string;
      writeScope: string;
      server: string;
    };
    labels: {
      path: string;
      resolved: string;
      exists: string;
      git: string;
      rules: string;
      type: string;
      checks: string;
      mode: string;
      sandbox: string;
      agent: string;
      persistedWD: string;
      requested: string;
      primaryRoot: string;
      rootN: (n: number) => string;
      cwd: string;
      python: string;
      none: string;
    };
    status: {
      yes: string;
      no: string;
    };
    serverCwdDiffers: string;
  };

  // Todo Panel
  todoPanel: {
    title: string;
    collapse: string;
    expand: string;
    closeTaskPlan: string;
    collapseTaskPlan: string;
    expandTaskPlan: string;
  };

  // Scope Settings
  scopeSettings: {
    codeModeDisabled: string;
    authorizeWorkspaces: string;
    noAuthorized: string;
    writeScopeTitle: string;
    writeScopeTooltip: string;
    writeScopeDescription: string;
  };

  // Team Selector
  teamSelector: {
    selectTeam: string;
    noTeams: string;
    memberCount: (count: number) => string;
    confirmDisband: (name: string) => string;
    disbandTeam: string;
    createTeam: string;
  };

  // Team Members Dialog
  teamMembers: {
    title: string;
    description: string;
    ownerDesc: string;
    memberDesc: string;
    viewerDesc: string;
    permissionsUpdated: string;
    updatePermissionsFailed: string;
    memberRemoved: string;
    removeMemberFailed: string;
    removeMember: string;
    speakerPolicy: string;
    speakerPolicyHint: string;
    policyFree: string;
    policyAdminOnly: string;
    policyRoundRobin: string;
    policyRollCall: string;
    policyModerated: string;
    mute: string;
    unmute: string;
    mutedBadge: string;
    policyUpdated: string;
    updatePolicyFailed: string;
    speakingAs: string;
    speakManual: string;
    speakViaTwin: string;
    hostedBy: string;
    delegationUpdated: string;
    updateDelegationFailed: string;
  };

  teamFloor: {
    yourTurn: string;
    speaking: string;
    floorOpen: string;
    pass: string;
    raiseHand: string;
    handRaised: string;
    raisedHands: string;
    grantFloor: string;
  };

  share: {
    share: string;
    shareTask: string;
    shareDescription: string;
    wechat: string;
    moments: string;
    copyLink: string;
    qrCode: string;
    openInBrowser: string;
    creatingLink: string;
    linkCopied: string;
    linkFailed: string;
    wechatQrTitle: string;
    momentsQrTitle: string;
    qrTitle: string;
    wechatQrHint: string;
    momentsQrHint: string;
    qrHint: string;
    localOnlyHint: string;
    stopSharing: string;
    sharingStopped: string;
    stopSharingFailed: string;
    unavailable: string;
    exportReplay: string;
  };

  // Team Join page
  teamJoin: {
    missingToken: string;
    invalidInvite: string;
    joinSuccess: (name: string) => string;
    joinFailed: string;
    guestName: string;
    title: string;
    description: string;
    loadingInvite: string;
    membersAndParticipants: (members: number, participants: number) => string;
    displayNamePlaceholder: string;
    joining: string;
    joinButton: string;
    applyButton: string;
    applying: string;
    approvalRequired: string;
    approvalRequiredDescription: string;
    requestPendingTitle: string;
    requestPendingDescription: string;
    requestSubmitted: string;
    requestRejected: string;
    requestWithdrawn: string;
    requestExpired: string;
    requestCancelled: string;
    requestApprovedButUnavailable: string;
    refreshStatus: string;
    withdrawRequest: string;
    withdrawFailed: string;
    statusCheckFailed: string;
    missingDestination: string;
  };

  // Evolution Indicator
  evolutionIndicator: {
    clickToView: string;
    rulesAndMemories: (rules: number, memories: number) => string;
    deltaRules: (count: number) => string;
    deltaMemories: (count: number) => string;
  };

  // DAG Debugger (TaskGraph visualization)
  dagDebugger: {
    title: string;
    taskDetails: string;
    timeline: string;
    activeTasks: string;
    stats: string;
    dryRun: string;
    noTaskFound: string;
    nodeStatus: {
      pending: string;
      running: string;
      completed: string;
      failed: string;
      cancelled: string;
    };
    columns: {
      nodeId: string;
      kind: string;
      status: string;
      startedAt: string;
      completedAt: string;
      duration: string;
      error: string;
    };
    topology: {
      totalNodes: string;
      totalEdges: string;
      maxParallelism: string;
      criticalPath: string;
      layers: string;
    };
    budget: {
      tokens: string;
      usd: string;
      latency: string;
    };
    activeTasksEmpty: string;
    statsSummary: {
      totalTasks: string;
      completedTasks: string;
      failedTasks: string;
      successRate: string;
      totalSteps: string;
      totalTokens: string;
      totalUsd: string;
      avgDuration: string;
    };
    dryRunResult: {
      valid: string;
      invalid: string;
      estimatedMinSteps: string;
    };
  };

  // Skill Market Web API
  skillMarketApi: {
    title: string;
    searchPlaceholder: string;
    categories: string;
    installed: string;
    install: string;
    uninstall: string;
    publish: string;
    noResults: string;
    installSuccess: (name: string) => string;
    uninstallSuccess: (name: string) => string;
    installFailed: string;
    uninstallFailed: string;
    skillNotFound: string;
    notInstalled: string;
    alreadyInstalled: string;
    publishReady: string;
    publishHint: string;
    version: string;
    author: string;
    tags: string;
    description: string;
  };

  // Knowledge Graph Persistence
  kgPersistence: {
    title: string;
    backend: string;
    backends: {
      memory: string;
      sqlite: string;
      kuzu: string;
    };
    status: {
      connected: string;
      disconnected: string;
      loading: string;
    };
    actions: {
      switchBackend: string;
      exportTriples: string;
      importTriples: string;
      runQuery: string;
      shortestPath: string;
      patternMatch: string;
    };
    kuzu: {
      notInstalled: string;
      installHint: string;
      query: string;
      cypherPlaceholder: string;
    };
    sqlite: {
      dbPath: string;
      exportSuccess: string;
      importSuccess: (count: number) => string;
    };
    export: {
      title: string;
      format: string;
      json: string;
      csv: string;
    };
    import: {
      title: string;
      fileLabel: string;
      success: string;
      failed: string;
    };
  };

  // Coordinator Health (HA)
  coordinatorHealth: {
    title: string;
    healthy: string;
    unhealthy: string;
    checking: string;
    coordinatorType: string;
    holderId: string;
    activeLeases: string;
    connectivity: {
      ok: string;
      failed: string;
    };
    leaseDetails: {
      scope: string;
      ttlRemaining: string;
      renewCount: string;
      renewFailures: string;
      status: string;
    };
    leaseStatus: {
      ok: string;
      warning: string;
      expired: string;
      degraded: string;
    };
    guardian: {
      title: string;
      start: string;
      stop: string;
      running: string;
      stopped: string;
      renewInterval: string;
      renewRatio: string;
      maxFailures: string;
    };
    errors: {
      noLeases: string;
      leaseExpired: string;
      leaseExpiringSoon: string;
      connectivityFailed: string;
    };
  };

  armsEditor: {
    saved: (agentId: string) => string;
    saveFailed: (msg: string) => string;
    loading: string;
    loadFailed: (msg: string) => string;
    description: string;
    armsTab: string;
    skillsTab: string;
    permissionsTab: string;
    routingTab: string;
    availableArmsLabel: string;
    selectedArmsCount: (selected: number, total: number) => string;
    filterAll: string;
    filterEnabled: string;
    filterDisabled: string;
    filterSelected: string;
    filterUnselected: string;
    noArmsFound: string;
    extraAffinityLabel: string;
    extraAffinityHint: string;
    extraAffinityPlaceholder: string;
    privateSkillsLabel: string;
    privateSkillsHint: string;
    selectedSkillsCount: (count: number) => string;
    skillSearchPlaceholder: string;
    skillMarketplaceLabel: string;
    skillCategoryAll: string;
    skillCategoryCustom: string;
    enableAllSkills: string;
    disableAllSkills: string;
    enableVisibleSkills: string;
    disableVisibleSkills: string;
    visibleSkillsCount: (visible: number, total: number) => string;
    skillSource: (source: string) => string;
    customSkillSource: string;
    noSkillsFound: string;
    permissionsLabel: string;
    permissionsHint: string;
    permissionEffectiveCount: (enabled: number, total: number) => string;
    permissionGlobalGate: string;
    permissionAgentGrant: string;
    permissionEffective: string;
    permissionEnabled: string;
    permissionDisabled: string;
    permissionAvailable: string;
    permissionUnavailable: string;
    permissionAgentDefault: string;
    permissionAgentGranted: string;
    permissionAgentDenied: string;
    permissionEffectiveHint: string;
    permissionBlockedByGlobal: string;
    permissionBlockedByAgent: string;
    permissionDefaultGrantHint: string;
    permissionUpdateFailed: (msg: string) => string;
    budgetLabel: string;
    budgetOverride: string;
    budgetDefault: string;
    budgetEditHint: (agentId: string) => string;
    reset: string;
    saveAndReload: string;
  };
  architecture: {
    title: string;
    subtitle: string;
    loading: string;
    rendering: string;
    loadFailed: string;
    retry: string;
    emptyTitle: string;
    emptyDescription: string;
    groups: {
      entry: string;
      diagrams: string;
      topics: string;
      coreOrgans: string;
    };
    docs: {
      readme: string;
      "core-path": string;
      "high-res-map": string;
      "high-res-mermaid": string;
      "chat-modes": string;
      "react-self-evo": string;
      "organ-tiering": string;
      "module-map": string;
      "organ-cerebrum": string;
      "organ-ganglia": string;
      "organ-beak": string;
      "organ-hearts": string;
      "organ-chromatophores": string;
    };
  };
  knowledgePanel: {
    graphView: string;
    listView: string;
    searchPlaceholder: string;
    entityTypes: {
      center: string;
      subject: string;
      object: string;
      neighbor: string;
    };
    controls: {
      filters: string;
      focus: string;
      groups: string;
      display: string;
      forces: string;
      evidence: string;
      labels: string;
      links: string;
      stars: string;
      autoRotate: string;
      confidence: string;
      degree: string;
      updated: string;
      nodeSize: string;
      linkWidth: string;
      linkDistance: string;
      spread: string;
      fitGraph: string;
    };
    nodeAndEdgeStats: (n: number, e: number) => string;
  };

  // Number Formatting
  numberFormat: {
    yi: string;
    wan: string;
  };

  // Deep Research Roles
  deepResearchRoles: {
    marketLandscape: {
      name: string;
      focus: string;
      deliverable: string;
      searchAngles: string[];
    };
    userNeeds: {
      name: string;
      focus: string;
      deliverable: string;
      searchAngles: string[];
    };
    productPricing: {
      name: string;
      focus: string;
      deliverable: string;
      searchAngles: string[];
    };
    channelSales: {
      name: string;
      focus: string;
      deliverable: string;
      searchAngles: string[];
    };
    skeptic: {
      name: string;
      focus: string;
      deliverable: string;
      searchAngles: string[];
    };
  };

  chatStreamingFooter: {
    researching: string;
    swarmCollaborating: string;
    collaborating: string;
    coding: string;
    processing: string;
    thinking: string;
    running: string;
    done: string;
    error: string;
    completed: string;
    agentCollaboration: string;
    viewMachine: string;
    viewResult: string;
    readyToReadEditVerify: string;
    readyToBreakdownAndGather: string;
    readyToExecuteTask: string;
    readyToHandleCodeTask: string;
    readyForAgentCollaboration: string;
    readyForDeepTask: string;
    readyToExecute: string;
    awaitingConfirmation: string;
    executionError: string;
    updatingPlan: string;
    planUpdated: string;
    collectingData: string;
    dataCollected: string;
    readingContext: string;
    contextRead: string;
    modifyingArtifacts: string;
    artifactsModified: string;
    runningVerification: string;
    verificationDone: string;
    coordinatingAgents: string;
    agentsCoordinated: string;
    processingTask: string;
    organizingResults: string;
  };

  metaSkills: {
    title: string;
    subtitle: string;
    loading: string;
    loadFailed: (msg: string) => string;
    empty: string;
    count: (n: number) => string;
    steps: (n: number) => string;
    affinity: string;
    budget: string;
    budgetTokens: (n: number) => string;
    budgetUsd: (n: number) => string;
    budgetLatency: (n: number) => string;
    viewDiagram: string;
    hideDiagram: string;
    diagramButton: string;
    collapseDiagram: string;
    diagramLoading: string;
    diagramFailed: (msg: string) => string;
    directionLabel: string;
    directionLR: string;
    directionTD: string;
    directionRL: string;
    directionBT: string;
    matchLabel: string;
    matchPlaceholder: string;
    matchButton: string;
    matchNoResult: (q: string) => string;
    matchResult: (q: string, name: string) => string;
    refresh: string;
    noAffinity: string;
    whenToUse: string;
  };

  // Desktop page
  desktop: {
    disabledTitle: string;
    disabledDescription: string;
    enableButton: string;
    pluginSettingsButton: string;
    backToWorkspaceButton: string;
    header: {
      workspaceTooltip: string;
      brand: string;
      accountModels: string;
      desktopAssistant: string;
      desktopCount: (count: number) => string;
      market: string;
      aiReady: string;
      wifi: string;
      notifications: string;
      quickSettings: string;
      date: (month: number, date: number, weekday: string) => string;
    };
    widget: {
      today: string;
      date: (
        year: number,
        month: number,
        date: number,
        weekday: string,
      ) => string;
    };
    searchPlaceholder: string;
    open: string;
    drawer: {
      title: string;
      loading: string;
      error: (error: string) => string;
      count: (count: number) => string;
      electronMode: string;
      archiveBadge: (moved: number) => string;
      autoArchiveTooltip: string;
      archiving: string;
      archive: string;
      undoTooltip: string;
      undoing: string;
      undo: string;
      closeAria: string;
      readFailed: string;
      retry: string;
      searchPlaceholder: string;
    };
    categories: {
      all: string;
      folder: string;
      app: string;
      image: string;
      document: string;
      package: string;
      other: string;
    };
    loadingItems: string;
    fallbackGroupTitle: string;
    empty: {
      noSearchResults: string;
      tryAnotherKeyword: string;
      noDesktopFiles: string;
      dropFilesHere: string;
      noFilesInCategory: string;
    };
    contextMenu: {
      open: string;
      archiveToCategory: string;
      delete: string;
      confirmTrash: (name: string) => string;
      trashing: string;
    };
    dock: {
      desktopFiles: string;
      systemMonitor: string;
      settings: string;
    };
    apps: {
      workspace: { name: string; subtitle: string };
      aiBrowser: { name: string; subtitle: string };
      localFiles: { name: string; subtitle: string };
      localApps: { name: string; subtitle: string };
      terminalLogs: { name: string; subtitle: string };
      settings: { name: string; subtitle: string };
    };
    placeholders: {
      browser: string;
      communication: string;
      notes: string;
      subtitle: string;
    };
    systemWidget: {
      title: string;
      cpu: string;
      memory: string;
      cores: string;
      uptime: (hours: number, minutes: number) => string;
    };
    weekdays: string[];
    errors: {
      listItems: string;
      refresh: string;
      archive: string;
      undo: string;
      move: string;
      archiveOnlyFiles: string;
      trash: string;
    };
    toasts: {
      noFilesToArchive: string;
      archived: (moved: number) => string;
      undone: (undone: number) => string;
      noUndoOperations: string;
      fileMoved: string;
      fileArchived: (name: string, folder: string) => string;
      trashed: (name: string) => string;
    };
  };

  // Remote Workspace collaboration (Task 8–11)
  remoteWorkspace: {
    switcherTitle: string;
    switcherAria: string;
    searchPlaceholder: string;
    empty: string;
    loading: string;
    loadFailed: (error: string) => string;
    addWorkspace: string;
    switchWorkspaceAria: (name: string) => string;
    activeWorkspace: string;
    typeLocal: string;
    typeSmb: string;
    typeNfs: string;
    typeWebdav: string;
    typeSftp: string;
    typeS3: string;
    mountTarget: string;

    mountDialog: {
      title: string;
      nameLabel: string;
      namePlaceholder: string;
      protocolLabel: string;
      pathLabel: string;
      pathPlaceholder: string;
      hostLabel: string;
      shareLabel: string;
      usernameLabel: string;
      passwordLabel: string;
      domainLabel: string;
      exportPathLabel: string;
      urlLabel: string;
      portLabel: string;
      identityFileLabel: string;
      endpointUrlLabel: string;
      bucketLabel: string;
      accessKeyLabel: string;
      secretKeyLabel: string;
      regionLabel: string;
      testConnection: string;
      testing: string;
      testOk: string;
      testFailed: (error: string) => string;
      create: string;
      creating: string;
      createFailed: (error: string) => string;
      credentialsHint: string;
    };

    members: {
      title: string;
      loading: string;
      empty: string;
      addMember: string;
      addMemberPlaceholder: string;
      roleOwner: string;
      roleEditor: string;
      roleReviewer: string;
      roleViewer: string;
      changeRoleAria: (name: string) => string;
      removeMemberAria: (name: string) => string;
      editingFile: (file: string) => string;
      editingNone: string;
      addFailed: (error: string) => string;
      removeFailed: (error: string) => string;
      roleChangeFailed: (error: string) => string;
    };

    lease: {
      locked: string;
      lockedBy: (name: string) => string;
      remaining: (seconds: number) => string;
      requestTakeover: string;
      takeoverSent: string;
      takeoverFailed: (error: string) => string;
    };

    // WorkDirSelector remote-tab labels (Task 12)
    localTab: string;
    remoteTab: string;
    remoteEmpty: string;
    remoteLoading: string;
    remoteLoadFailed: (error: string) => string;
  };

  // Deep Research Panel
  deepResearchPanel: {
    title: string;
    cancelAgentRunTitle: string;
    cancelRunConfirmTitle: string;
    cancelRunConfirmDescription: (count: number) => string;
    cancelRunConfirmLabel: string;
    copyReportFailedToast: string;
    metricRoles: string;
    metricSources: string;
    metricMaterials: string;
    agentBudget: string;
    searchesCount: (n: number) => string;
    batchProgress: (completed: number, total: number) => string;
    batchFailedCancelled: (failed: number, cancelled: number) => string;
    batchIdLabel: (id: string) => string;
    liveAgentStream: string;
    eventsCount: (n: number) => string;
    prefetch: string;
    prefetchStats: (runs: number, evidence: number) => string;
    executionSteps: string;
    synthesisRoleLabel: string;
    searchSources: string;
    evidence: string;
    finalReport: string;
    savedToLeadMemory: string;
    copied: string;
    copyMarkdown: string;
    downloadMarkdown: string;
    stageSummary: string;
    openUrl: string;
    hitsCount: (n: number) => string;
    evidenceCount: (n: number) => string;
    batchEventTitle: (status: string) => string;
    subagentEventTitle: (name: string, status: string) => string;
    subagentFallback: string;
    statusComplete: string;
    statusUpdated: string;
    routeBlocked: string;
    routeWarning: string;
    routeAllowed: string;
  };

  // Desktop Organizer Page
  desktopOrganizerPage: {
    title: string;
    description: string;
    enabledOn: string;
    enabledOff: string;
    tileNotTakeoverTitle: string;
    tileNotTakeoverBody: string;
    tileRightClickTitle: string;
    tileRightClickBody: string;
    tileSafePreviewTitle: string;
    tileSafePreviewBody: string;
    webEnvNotice: string;
    contextMenuTitle: string;
    contextMenuDescription: string;
    installButton: string;
    installingButton: string;
    removeButton: string;
    removingButton: string;
    installSuccess: string;
    installUnsupported: string;
    removeSuccess: string;
    removeUnsupported: string;
    openAssistant: string;
    backToWorkspace: string;
    confirmRemoveTitle: string;
    confirmRemoveDescription: string;
  };

  // Knowledge page
  knowledge: {
    comingSoon: string;
    tabFiles: string;
    memoryManagement: string;
    wikiDocs: string;
    fileManagement: string;
  };

  // Work block labels (template strings with {var} placeholders)
  workBlocks: {
    actions: {
      awaitVerification: string;
      spawnAgent: string;
      finishAgent: string;
      writeTodoList: string;
      parallelDispatch: string;
      submitResult: string;
      loadSkill: string;
      terminalFailed: string;
      terminalRecovered: string;
      runTerminal: string;
      read: string;
      createFile: string;
      deleteFile: string;
      editFile: string;
      browse: string;
      search: string;
      execute: string;
    };
    actionTarget: string;
    spawnAgent: string;
    finishAgent: string;
    parallelDispatch: string;
    parallelDispatchWithCount: string;
    parallelTarget: string;
    parallelTargetWithCount: string;
    skillNamed: string;
    skillDeepResearch: string;
    skillReportWriting: string;
    skillDocx: string;
    connectModel: string;
    subagentFallback: string;
  };

  // Storage (local knowledge base) page
  storage: {
    defaultQuery: string;
    libraries: {
      overviewLabel: string;
      overviewDetail: string;
      appsLabel: string;
      appsDetail: string;
      docsLabel: string;
      docsDetail: string;
      imagesLabel: string;
      imagesDetail: string;
      videosLabel: string;
      videosDetail: string;
      computerLabel: string;
      computerDetail: string;
      sourcesLabel: string;
      sourcesDetail: string;
    };
    service: {
      credentialsExpired: string;
      notFound: string;
      startFailed: string;
      notConnected: string;
      networkError: string;
    };
    toolbar: {
      authorize: string;
      scan: string;
      privacy: string;
      efficiency: string;
      online: string;
      offline: string;
      reconnecting: string;
      reconnect: string;
      searchPlaceholder: string;
      searchAria: string;
      searchIn: string;
      scopeFilterAria: string;
      gridViewAria: string;
      listViewAria: string;
      filterAria: string;
      sortAria: string;
    };
    overview: {
      tabAll: string;
      tabDocs: string;
      tabImages: string;
      tabRecent: string;
      indexingTitle: string;
      indexingDesc: string;
      aggregateDesc: string;
      localDatabaseBadge: string;
      previewTitle: string;
      previewSubtitle: string;
      itemsWithStatus: string;
    };
    docs: {
      title: string;
      subtitle: string;
      searchLabel: string;
      allDocs: string;
      indexNote: string;
      badgeRecent: string;
      badgeLocalDocs: string;
      colName: string;
      colLocation: string;
      colSize: string;
      colModified: string;
      colActions: string;
      footerNote: string;
    };
    images: {
      title: string;
      subtitle: string;
      searchLabel: string;
      badgeAllImages: string;
      filterAll: string;
      filterOcr: string;
      filterLocalLibrary: string;
      ocrBadge: string;
    };
    videos: {
      title: string;
      subtitle: string;
      searchLabel: string;
      badgeAllVideos: string;
      indexNote: string;
      indexAction: string;
      indexing: string;
      noResults: string;
      colName: string;
      colLocation: string;
      colSize: string;
      colDuration: string;
      colModified: string;
      colActions: string;
      footerNote: string;
      tabVideos: string;
      tabPeople: string;
      tabTags: string;
      searchPlaceholder: string;
      searchHint: string;
      noIndex: string;
      noFaces: string;
      noTags: string;
      noOcr: string;
      summary: string;
      cover: string;
      duration: string;
      peopleCount: (n: number) => string;
      faceCount: (n: number) => string;
      player: {
        open: string;
        close: string;
        prev: string;
        next: string;
        atTime: (t: string) => string;
      };
      ocr: {
        label: string;
        hint: string;
      };
    };
    apps: {
      title: string;
      subtitle: string;
      searchLabel: string;
      registeredTitle: string;
      registeredSubtitle: string;
      badgeList: string;
      colName: string;
      colType: string;
      colStatus: string;
      colActions: string;
      open: string;
      actions: string;
      typeSystemApp: string;
      typeImagePdf: string;
      typeDocsSheets: string;
      typeWebResources: string;
      typeSystemTool: string;
      typeDownloadManager: string;
      statusRegistered: string;
      statusPendingScan: string;
      statusCallable: string;
      statusFolder: string;
    };
    computer: {
      searchLabel: string;
      currentDirBadge: string;
      itemsCount: string;
      stayNote: string;
      colName: string;
      colType: string;
      colItems: string;
      footerOnline: string;
      footerOffline: string;
      folderType: string;
    };
    sources: {
      title: string;
      subtitle: string;
      add: string;
      scanQueueAria: string;
      privacyPolicyAria: string;
      metricSources: string;
      metricFiles: string;
      metricChunks: string;
      reconnectTitle: string;
      notConnected: string;
      badgeLocalIndex: string;
      badgeNoUpload: string;
      colDirectory: string;
      colFiles: string;
      colChunks: string;
      colStatus: string;
      emptyTitleOnline: string;
      emptyTitleOffline: string;
      emptyDescOnline: string;
      emptyDescOffline: string;
      addFolder: string;
      viewPrivacyPolicy: string;
      footerPrivacy: string;
      footerQueue: string;
      filesCount: string;
      chunksCount: string;
      statusReady: string;
      statusPending: string;
      removeTitle: string;
      removeDesc: string;
      removeConfirm: string;
      remove: string;
    };
    search: {
      backTo: string;
      resultsTitle: string;
      statusTitle: string;
      hitsSummary: string;
      noHitsSummary: string;
      continueLabel: string;
      quoteSelected: string;
      engineNotAttached: string;
      noMatch: string;
      noMatchHint: string;
      viewSources: string;
      switchPrivacyMode: string;
    };
    preview: {
      sourceLocation: string;
      typeLabel: string;
      updatedLabel: string;
      sizeLabel: string;
      snippetTitle: string;
      snippetDesc: string;
      quoteInChat: string;
      openLocation: string;
      actionPreview: string;
      actionQuote: string;
      actionLocate: string;
    };
    topics: {
      docsAllTitle: string;
      docsAllSubtitle: string;
      docsAllStatus: string;
      docsSourcesTitle: string;
      docsSourcesSubtitle: string;
      docsSourcesStatus: string;
      docsTopicsTitle: string;
      docsTopicsSubtitle: string;
      docsTopicsStatus: string;
      docsRecentTitle: string;
      docsRecentSubtitle: string;
      docsRecentStatus: string;
      imagesAllTitle: string;
      imagesAllSubtitle: string;
      imagesAllStatus: string;
      imagesTopicsTitle: string;
      imagesTopicsSubtitle: string;
      imagesTopicsStatus: string;
      imagesSourcesTitle: string;
      imagesSourcesSubtitle: string;
      imagesSourcesStatus: string;
      imagesOcrTitle: string;
      imagesOcrSubtitle: string;
      imagesOcrStatus: string;
      coverWork: string;
      coverProject: string;
      coverDownloads: string;
      coverContract: string;
      coverTech: string;
      coverResearch: string;
      coverToday: string;
      cover7Days: string;
      cover30Days: string;
      coverPeople: string;
      coverPlaces: string;
      coverTheme: string;
      coverDesktop: string;
      coverWechat: string;
      coverWhiteboard: string;
      coverInterface: string;
      coverSpreadsheet: string;
    };
    demoFiles: {
      doc1Name: string;
      doc1Kind: string;
      doc1Updated: string;
      doc2Name: string;
      doc2Kind: string;
      doc2Updated: string;
      doc3Name: string;
      doc3Kind: string;
      doc3Updated: string;
      doc4Name: string;
      doc4Kind: string;
      doc4Updated: string;
      doc5Name: string;
      doc5Kind: string;
      doc5Updated: string;
      image1Kind: string;
      image1Updated: string;
      image2Name: string;
      image2Kind: string;
      image2Updated: string;
      image3Name: string;
      image3Kind: string;
      image3Updated: string;
      image4Name: string;
      image4Kind: string;
      image4Updated: string;
    };
  };
}
