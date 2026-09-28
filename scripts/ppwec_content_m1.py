"""PPWEC Module 1 content — Professionalism at the Workplace.

Authored from the master content pack (Part 2). Content governance (§25):
this file is the learning intent — changes to learning objectives, core
messages, case conclusions, assessment answers, organizational values and
compliance statements must be referred back to the content owner (Head
Field HR) before implementation. Screen layout / interaction mechanics
may be adapted by IT as long as learning intent is preserved.
"""

# ─── 18-module architecture (§5) ─────────────────────────────────────────────

CATALOG = [
    (1, 'Professionalism at the Workplace', 'Building the Foundation of Professional Excellence'),
    (2, 'Effective Communication & Interpersonal Excellence', 'Communicate to Connect, Influence and Inspire'),
    (3, 'Ownership, Accountability & Responsibility', 'Think Like an Owner'),
    (4, 'Time Management, Prioritization & Personal Productivity', 'Work Smarter. Deliver Better.'),
    (5, 'Collaboration & Teamwork', 'Winning Together Across Functions'),
    (6, 'Customer Excellence', 'Creating Exceptional Customer Experiences'),
    (7, 'Growth Mindset & Continuous Learning', 'Learn, Adapt and Grow Every Day'),
    (8, 'Ethics, Integrity & Organizational Values', 'Doing the Right Thing, Every Time'),
    (9, 'Innovation, Creativity & Continuous Improvement', 'Think Better. Improve Continuously.'),
    (10, 'Emotional Intelligence & Self-Leadership', 'Master Yourself to Lead Others'),
    (11, 'Problem Solving & Decision Making', 'Making Better Decisions Every Day'),
    (12, 'Negotiation, Conflict Resolution & Influencing Skills', 'Building Win-Win Relationships'),
    (13, 'Leadership Without Authority', 'Influence Through Behaviour, Not Position'),
    (14, 'Change Agility & Resilience', 'Thriving in a Changing World'),
    (15, 'Quality Culture, Compliance & Patient Safety', 'Every Decision Protects a Patient'),
    (16, 'Business & Financial Acumen', 'Understanding How Business Creates Value'),
    (17, 'Digital Excellence, AI & Future Skills', 'Becoming Future Ready'),
    (18, 'Execution Excellence, Accountability & Results Orientation', 'Commit. Execute. Deliver.'),
]

MODULE_1_BADGE = 'Professional Excellence Explorer'

# ─── Module 1 screens (Part 2, sections D–U) ─────────────────────────────────
# on_screen_text: list of content blocks the player renders.
# interaction_payload.options: [{key, text, correct, feedback, consequence}]

SCREENS = [
    {
        'screen_number': 1,
        'section': 'opening',
        'title': 'What Does Professionalism Really Mean?',
        'on_screen_text': [
            {'type': 'heading', 'text': 'Imagine two employees with identical qualifications.'},
            {'type': 'text', 'text': 'Both are technically capable. Both know their jobs. But one arrives prepared, keeps commitments, communicates respectfully, accepts feedback, takes ownership and learns continuously. The other frequently needs reminders, blames others, ignores messages, resists feedback and misses commitments.'},
        ],
        'voice_over': 'Professionalism is much more than qualifications, designation or experience. It is the way we consistently show up, communicate, make decisions, treat people and honour our commitments.',
        'visual_direction': 'Split-screen comparison of two professionals in the same workplace. Subtle visual cues — no exaggerated stereotypes.',
        'interaction_type': 'choice',
        'interaction_payload': {
            'prompt': 'Who would you trust with an important responsibility?',
            'options': [
                {'key': 'A', 'text': 'Employee A — Professional', 'correct': True,
                 'feedback': 'Technical competence may get you into a role. Professional behaviour determines how much others can trust you with responsibility.'},
                {'key': 'B', 'text': 'Employee B — Technically Competent but Unprofessional', 'correct': False,
                 'feedback': 'Technical competence alone cannot create excellence. Reliability, respect and ownership are what make others trust you with responsibility.'},
            ],
        },
        'estimated_seconds': 120,
    },
    {
        'screen_number': 2,
        'section': 'learning',
        'title': 'A Simple Definition',
        'on_screen_text': [
            {'type': 'heading', 'text': 'Professionalism means:'},
            {'type': 'callout', 'text': 'Consistently demonstrating the behaviours, standards and attitudes expected of a trusted professional.'},
            {'type': 'text', 'text': 'It includes five pillars — click each to see an example.'},
        ],
        'voice_over': 'Professionalism combines what you know, how you behave and how reliably you deliver. It is competence supported by character, conduct, commitment and continuous learning.',
        'interaction_type': 'click_reveal',
        'interaction_payload': {
            'items': [
                {'key': 'competence', 'title': 'Competence', 'text': 'Doing your work well.', 'example': 'Delivering an accurate batch record review the first time.'},
                {'key': 'character', 'title': 'Character', 'text': 'Doing the right thing.', 'example': 'Reporting a documentation discrepancy even when nobody would notice.'},
                {'key': 'conduct', 'title': 'Conduct', 'text': 'Behaving respectfully.', 'example': 'Speaking courteously to every colleague, from operator to director.'},
                {'key': 'commitment', 'title': 'Commitment', 'text': 'Doing what you said you would do.', 'example': 'Delivering the promised report by 5 PM — or flagging early if you cannot.'},
                {'key': 'learning', 'title': 'Continuous Learning', 'text': 'Remaining open to improvement.', 'example': 'Asking for feedback after a presentation and acting on it.'},
            ],
        },
        'estimated_seconds': 150,
    },
    {
        'screen_number': 3,
        'section': 'learning',
        'title': 'Professionalism Is Visible',
        'on_screen_text': [
            {'type': 'text', 'text': 'People experience your professionalism through the way you communicate, respond, handle pressure, treat others, manage time, deliver quality, handle mistakes and represent Pulse.'},
            {'type': 'pulse_moment', 'text': 'Every interaction is a moment of truth.'},
        ],
        'voice_over': 'Professionalism is not something hidden inside us. People experience it through our everyday behaviour.',
        'interaction_type': 'content',
        'estimated_seconds': 60,
    },
    {
        'screen_number': 4,
        'section': 'learning',
        'title': 'The Pulse Professional',
        'on_screen_text': [
            {'type': 'text', 'text': 'A Pulse professional: THINKS professionally. SPEAKS respectfully. ACTS ethically. LEARNS continuously. COLLABORATES constructively. DELIVERS reliably. REPRESENTS Pulse with pride.'},
        ],
        'voice_over': 'These seven behaviours define a Pulse professional. Click each behaviour to see it in action.',
        'interaction_type': 'click_reveal',
        'interaction_payload': {
            'items': [
                {'key': 'thinks', 'title': 'THINKS', 'text': 'Professionally.', 'example': 'Considers the impact of decisions on patients and colleagues.'},
                {'key': 'speaks', 'title': 'SPEAKS', 'text': 'Respectfully.', 'example': 'Disagrees with the idea, never attacks the person.'},
                {'key': 'acts', 'title': 'ACTS', 'text': 'Ethically.', 'example': 'Follows the approved process even under deadline pressure.'},
                {'key': 'learns', 'title': 'LEARNS', 'text': 'Continuously.', 'example': 'Asks "What can I learn?" instead of "Why me?".'},
                {'key': 'collaborates', 'title': 'COLLABORATES', 'text': 'Constructively.', 'example': 'Brings solutions, not just problems, to cross-functional meetings.'},
                {'key': 'delivers', 'title': 'DELIVERS', 'text': 'Reliably.', 'example': 'Promises, plans, executes, communicates and delivers.'},
                {'key': 'represents', 'title': 'REPRESENTS', 'text': 'Pulse with pride.', 'example': 'Acts as an ambassador of the organization in every interaction.'},
            ],
        },
        'estimated_seconds': 150,
    },
    {
        'screen_number': 5,
        'section': 'reflection',
        'title': 'Professionalism Starts Before the Work Begins',
        'on_screen_text': [
            {'type': 'text', 'text': 'Before starting your day, ask: Am I prepared? Do I know my priorities? Have I reviewed my commitments? Do I have the information I need? Is there anything I need to clarify?'},
        ],
        'interaction_type': 'content',
        'is_mandatory': False,
        'estimated_seconds': 60,
        # Reflection storage intentionally deferred per scope decision.
    },
    {
        'screen_number': 6,
        'section': 'learning',
        'title': 'Your Professional Presence',
        'on_screen_text': [
            {'type': 'text', 'text': 'Professional presence includes appropriate appearance, punctuality, preparedness, body language, communication, attentiveness, digital etiquette and respectful behaviour.'},
            {'type': 'note', 'text': 'Neat, appropriate, role-appropriate and professional — never about fashion or status.'},
        ],
        'voice_over': 'Professional presence is not about fashion or status. It is about demonstrating respect for yourself, others and the workplace.',
        'interaction_type': 'content',
        'estimated_seconds': 60,
    },
    {
        'screen_number': 7,
        'section': 'learning',
        'title': 'Punctuality Is a Professional Statement',
        'on_screen_text': [
            {'type': 'story', 'text': 'A virtual review is scheduled for 10:00 AM. At 10:02 one participant is still joining. At 10:05 another says "I just saw the invite." At 10:10 the meeting finally starts. Five people have lost ten minutes each.'},
        ],
        'interaction_type': 'choice',
        'interaction_payload': {
            'prompt': 'What has actually been lost?',
            'options': [
                {'key': 'A', 'text': 'Only ten minutes', 'correct': False,
                 'feedback': 'The cost is much larger than the clock suggests — five people lost ten minutes each.'},
                {'key': 'B', 'text': 'Time, productivity and professional credibility', 'correct': True,
                 'feedback': 'Punctuality is not simply about the clock. It communicates reliability and respect for other people\'s time.'},
                {'key': 'C', 'text': 'Nothing significant', 'correct': False,
                 'feedback': 'Repeated lateness erodes both productivity and how others perceive your reliability.'},
            ],
        },
        'estimated_seconds': 90,
    },
    {
        'screen_number': 8,
        'section': 'learning',
        'title': 'Preparedness',
        'on_screen_text': [
            {'type': 'text', 'text': 'A professional does not enter an important meeting asking "So... what are we discussing?"'},
            {'type': 'checklist', 'items': ['Review the agenda.', 'Read relevant material.', 'Know the objective.', 'Prepare questions.', 'Bring required data.', 'Be ready to contribute.']},
            {'type': 'pulse_principle', 'text': 'Preparation demonstrates respect — for the work and for the people involved.'},
        ],
        'interaction_type': 'content',
        'estimated_seconds': 60,
    },
    {
        'screen_number': 9,
        'section': 'learning',
        'title': 'How You Communicate Matters',
        'on_screen_text': [
            {'type': 'text', 'text': 'Professional communication should be: Clear, Concise, Courteous, Complete, Constructive and Timely.'},
        ],
        'voice_over': 'Six characteristics separate professional communication from everyday noise. Drag each one into the circle.',
        'interaction_type': 'drag_sort',
        'interaction_payload': {
            'prompt': 'Drag the six characteristics of professional communication into the circle.',
            'targets': [{'key': 'circle', 'label': 'Professional Communication', 'accepts': ['clear', 'concise', 'courteous', 'complete', 'constructive', 'timely']}],
            'items': [
                {'key': 'clear', 'text': 'Clear — say what needs to be understood.'},
                {'key': 'concise', 'text': 'Concise — respect the recipient\'s time.'},
                {'key': 'courteous', 'text': 'Courteous — be respectful even when disagreeing.'},
                {'key': 'complete', 'text': 'Complete — provide necessary context.'},
                {'key': 'constructive', 'text': 'Constructive — focus on solutions.'},
                {'key': 'timely', 'text': 'Timely — respond appropriately.'},
            ],
        },
        'estimated_seconds': 120,
    },
    {
        'screen_number': 10,
        'section': 'learning',
        'title': 'The Email Test',
        'on_screen_text': [
            {'type': 'scenario', 'text': 'You receive an email that you strongly disagree with. Which response is most professional?'},
        ],
        'interaction_type': 'choice',
        'interaction_payload': {
            'prompt': 'Choose your response.',
            'options': [
                {'key': 'A', 'text': '"This is completely wrong. Please check your facts before sending such emails."', 'correct': False,
                 'feedback': 'Attacking the person escalates conflict and damages trust — even if the facts are on your side.'},
                {'key': 'B', 'text': '"I don\'t agree with this approach. Let\'s discuss."', 'correct': False,
                 'feedback': 'Better than attacking, but it gives no context and invites friction instead of resolution.'},
                {'key': 'C', 'text': '"Thank you for sharing this. I see the situation differently based on the available information. May we discuss the key points and agree on the best way forward?"', 'correct': True,
                 'feedback': 'Professional disagreement focuses on the issue rather than attacking the person.'},
            ],
        },
        'estimated_seconds': 90,
    },
    {
        'screen_number': 11,
        'section': 'learning',
        'title': 'Digital Professionalism',
        'on_screen_text': [
            {'type': 'text', 'text': 'Professionalism applies equally to email, WhatsApp used for work, video meetings, Darwinbox, collaboration platforms, shared documents and social media when representing the organization.'},
            {'type': 'checklist', 'items': ['Use appropriate language.', 'Respond within reasonable timelines.', 'Avoid unnecessary messages.', 'Protect confidential information.', 'Check before forwarding.', 'Maintain professional tone.', 'Assume written communication may be seen by others.']},
            {'type': 'pulse_moment', 'text': 'Digital communication is still human communication.'},
        ],
        'interaction_type': 'content',
        'estimated_seconds': 75,
    },
    {
        'screen_number': 12,
        'section': 'learning',
        'title': 'The Camera Is On',
        'on_screen_text': [
            {'type': 'scenario', 'text': 'You join an important virtual review. Your camera is off. You are responding to messages while the meeting continues. When asked for your input, you say: "Sorry, I wasn\'t listening."'},
        ],
        'interaction_type': 'choice',
        'interaction_payload': {
            'prompt': 'What does this behaviour communicate?',
            'options': [
                {'key': 'A', 'text': 'Multitasking efficiency', 'correct': False,
                 'feedback': 'Splitting attention means neither task gets your best — and colleagues feel the disengagement.'},
                {'key': 'B', 'text': 'Lack of engagement', 'correct': True,
                 'feedback': 'Being physically present and being professionally present are not always the same thing.'},
                {'key': 'C', 'text': 'Professional flexibility', 'correct': False,
                 'feedback': 'Flexibility is valuable, but disengagement in an important review signals disrespect.'},
                {'key': 'D', 'text': 'Nothing significant', 'correct': False,
                 'feedback': 'Others notice disengagement — it silently shapes your professional reputation.'},
            ],
        },
        'estimated_seconds': 90,
    },
    {
        'screen_number': 13,
        'section': 'learning',
        'title': 'Professionalism Means Respect',
        'on_screen_text': [
            {'type': 'checklist', 'items': ['Listening without interrupting.', 'Valuing different perspectives.', 'Speaking respectfully.', 'Giving credit.', 'Avoiding humiliation.', 'Treating support staff with dignity.', 'Respecting time.', 'Respecting boundaries.', 'Disagreeing without disrespect.']},
            {'type': 'pulse_moment', 'text': 'Designation determines responsibility. It does not determine human worth.'},
        ],
        'interaction_type': 'content',
        'estimated_seconds': 75,
    },
    {
        'screen_number': 14,
        'section': 'learning',
        'title': 'The Respect Challenge',
        'on_screen_text': [
            {'type': 'scenario', 'text': 'A senior employee disagrees strongly with a junior colleague during a meeting. The junior colleague makes an incorrect statement. What should the senior employee do?'},
        ],
        'interaction_type': 'choice',
        'interaction_payload': {
            'prompt': 'Choose the best response.',
            'options': [
                {'key': 'A', 'text': '"That\'s completely wrong. You clearly don\'t understand the issue."', 'correct': False,
                 'feedback': 'Public humiliation silences future contributions and damages trust across the team.'},
                {'key': 'B', 'text': '"Let\'s examine the data together. I may be seeing it differently."', 'correct': True,
                 'feedback': 'Professionalism allows us to correct mistakes without demeaning or diminishing people.'},
                {'key': 'C', 'text': 'Ignore the comment.', 'correct': False,
                 'feedback': 'Leaving an incorrect statement unaddressed can allow errors to propagate — correct respectfully instead.'},
            ],
        },
        'estimated_seconds': 90,
    },
    {
        'screen_number': 15,
        'section': 'learning',
        'title': 'Listening Is a Professional Skill',
        'on_screen_text': [
            {'type': 'text', 'text': 'Professional listening means: listen to understand — not listen to respond.'},
            {'type': 'checklist', 'items': ['Give attention.', 'Don\'t interrupt unnecessarily.', 'Clarify when needed.', 'Summarize important points.']},
        ],
        'interaction_type': 'choice',
        'interaction_payload': {
            'prompt': 'Mini interaction: In a workplace conversation, a colleague keeps finishing the speaker\'s sentences and answers before the question ends. What did they do?',
            'options': [
                {'key': 'A', 'text': 'Listened effectively', 'correct': False, 'feedback': 'Interrupting is the opposite of effective listening.'},
                {'key': 'B', 'text': 'Interrupted', 'correct': True, 'feedback': 'Listening to respond instead of listening to understand leads to interrupting.'},
                {'key': 'C', 'text': 'Assumed', 'correct': False, 'feedback': 'Assumption may be involved, but the visible behaviour here is interrupting.'},
                {'key': 'D', 'text': 'Clarified', 'correct': False, 'feedback': 'Clarifying means asking after understanding — not cutting in early.'},
            ],
        },
        'estimated_seconds': 90,
    },
    {
        'screen_number': 16,
        'section': 'learning',
        'title': 'Do What You Say',
        'on_screen_text': [
            {'type': 'text', 'text': 'Professional credibility is built through commitments: Commit → Plan → Execute → Communicate → Deliver.'},
            {'type': 'pulse_principle', 'text': 'A commitment creates an expectation.'},
        ],
        'voice_over': 'Every commitment you make sets an expectation in someone else\'s mind. Honouring it is the engine of professional credibility.',
        'interaction_type': 'content',
        'estimated_seconds': 60,
    },
    {
        'screen_number': 17,
        'section': 'learning',
        'title': 'When You Cannot Meet a Commitment',
        'on_screen_text': [
            {'type': 'scenario', 'text': 'You promised a report by 5 PM. At 3 PM you realize that a dependency has delayed your work. What should you do?'},
        ],
        'interaction_type': 'choice',
        'interaction_payload': {
            'prompt': 'Choose your response.',
            'options': [
                {'key': 'A', 'text': 'Wait until 5 PM and explain afterward.', 'correct': False,
                 'feedback': 'Late information is almost as costly as no information — the stakeholder loses the chance to adjust plans.'},
                {'key': 'B', 'text': 'Ignore the issue and hope the dependency gets resolved.', 'correct': False,
                 'feedback': 'Hope is not a plan — unmanaged commitments break trust faster than delayed ones.'},
                {'key': 'C', 'text': 'Inform the stakeholder early, explain the issue and propose a realistic revised timeline.', 'correct': True,
                 'feedback': 'Professional accountability includes early communication — not just eventual delivery.'},
            ],
        },
        'estimated_seconds': 90,
    },
    {
        'screen_number': 18,
        'section': 'learning',
        'title': 'Reliability Builds Trust',
        'on_screen_text': [
            {'type': 'animation', 'text': 'Promise → Deliver → Promise → Deliver → Promise → Deliver ⇒ TRUST. But: Promise → Miss → Excuse, repeatedly ⇒ TRUST DECLINES.'},
        ],
        'voice_over': 'Trust is rarely created by one dramatic action. It is built through repeated evidence of reliability.',
        'interaction_type': 'content',
        'estimated_seconds': 60,
    },
    {
        'screen_number': 19,
        'section': 'learning',
        'title': 'Professionalism When Things Go Wrong',
        'on_screen_text': [
            {'type': 'text', 'text': 'Anyone can appear professional when everything is easy. Professionalism becomes visible when deadlines are missed, customers complain, mistakes happen, pressure increases, plans change or someone disagrees with you.'},
            {'type': 'text', 'text': 'Ask yourself: Do I become defensive — or constructive? Do I blame — or solve? Do I hide problems — or escalate them responsibly?'},
        ],
        'interaction_type': 'content',
        'estimated_seconds': 75,
    },
    {
        'screen_number': 20,
        'section': 'learning',
        'title': 'The Pressure Test',
        'on_screen_text': [
            {'type': 'scenario', 'text': 'A customer escalation reaches you. You believe another department caused the problem. What is the most professional first response?'},
        ],
        'interaction_type': 'choice',
        'interaction_payload': {
            'prompt': 'Choose your first response.',
            'options': [
                {'key': 'A', 'text': '"That is not our department\'s fault."', 'correct': False,
                 'feedback': 'Deflection leaves the customer unattended and signals blame-shifting.'},
                {'key': 'B', 'text': '"Let me understand what happened, address the immediate customer need and then work with the relevant team to identify the root cause."', 'correct': True,
                 'feedback': 'Professionalism does not mean ignoring responsibility. It means solving the problem while addressing the underlying cause. — MD philosophy: focus on solutions rather than problems.'},
                {'key': 'C', 'text': '"Please contact the other department."', 'correct': False,
                 'feedback': 'Passing the customer around multiplies their frustration and abdicates ownership.'},
            ],
        },
        'md_philosophy': True,
        'estimated_seconds': 90,
    },
    {
        'screen_number': 21,
        'section': 'learning',
        'title': 'Owning Mistakes',
        'on_screen_text': [
            {'type': 'steps', 'items': [
                'Acknowledge — "I made an error."',
                'Understand — "Let me understand what caused it."',
                'Correct — "Here is what I will do now."',
                'Prevent — "Here is how I will avoid repeating it."',
                'Learn — "What should I learn from this?"',
            ]},
            {'type': 'pulse_moment', 'text': 'A mistake can be an event. Repeating the same mistake without learning becomes a behaviour.'},
        ],
        'interaction_type': 'content',
        'estimated_seconds': 75,
    },
    {
        'screen_number': 22,
        'section': 'learning',
        'title': 'Stay Curious',
        'on_screen_text': [
            {'type': 'text', 'text': 'A professional never says "I already know everything I need to know." Instead asks: "What can I learn?"'},
            {'type': 'checklist', 'items': ['Seek feedback.', 'Learn from mistakes.', 'Ask questions.', 'Learn from colleagues.', 'Explore new methods.', 'Upgrade skills.', 'Accept changing expectations.']},
        ],
        'md_philosophy': True,
        'interaction_type': 'content',
        'estimated_seconds': 75,
    },
    {
        'screen_number': 23,
        'section': 'learning',
        'title': 'Your Limitation ≠ The Organization\'s Limitation',
        'on_screen_text': [
            {'type': 'scenario', 'text': 'A new digital process is introduced. An employee says: "I don\'t know how to use it, so this system won\'t work for our team."'},
        ],
        'interaction_type': 'choice',
        'interaction_payload': {
            'prompt': 'What is the better professional response?',
            'options': [
                {'key': 'A', 'text': 'Continue using the old system.', 'correct': False,
                 'feedback': 'Rejecting new tools makes a personal gap the team\'s ceiling.'},
                {'key': 'B', 'text': 'Ask for training, practise and identify how the new system can improve the work.', 'correct': True,
                 'feedback': 'Professional growth begins when we treat capability gaps as learning opportunities. — MD philosophy: do not make personal limitations the organization\'s limitations.'},
                {'key': 'C', 'text': 'Wait until someone else learns it.', 'correct': False,
                 'feedback': 'Waiting transfers your growth debt to colleagues and delays the team.'},
            ],
        },
        'md_philosophy': True,
        'estimated_seconds': 90,
    },
    {
        'screen_number': 24,
        'section': 'learning',
        'title': 'Professionalism in PRICE',
        'on_screen_text': [
            {'type': 'text', 'text': 'Professionalism is not separate from our values. It brings them to life.'},
        ],
        'interaction_type': 'click_reveal',
        'interaction_payload': {
            'items': [
                {'key': 'P', 'title': 'P — Passion for Purpose', 'text': 'Understand why your work matters.', 'example': 'Link every task to the patient who ultimately benefits.'},
                {'key': 'R', 'title': 'R — Respect for Resources', 'text': 'Use time, money, information and assets responsibly.', 'example': 'Avoid wasteful printing, guard confidential data, respect everyone\'s time.'},
                {'key': 'I', 'title': 'I — Innovation for Excellence', 'text': 'Look for better ways to work.', 'example': 'Suggest a small process improvement after every project.'},
                {'key': 'C', 'title': 'C — Collaboration for Co-creation', 'text': 'Work with others to create better outcomes.', 'example': 'Involve QA early instead of surprising them at the end.'},
                {'key': 'E', 'title': 'E — Express Execution', 'text': 'Convert commitments into results.', 'example': 'Close the loop on every action item you own.'},
            ],
        },
        'pulse_anchor': 'PRICE',
        'estimated_seconds': 150,
    },
    {
        'screen_number': 25,
        'section': 'learning',
        'title': 'Professionalism and Patient Impact',
        'on_screen_text': [
            {'type': 'text', 'text': 'You may never meet the patient who ultimately benefits from your work. But your professionalism can influence product quality, supply reliability, customer trust, accurate information, ethical practices, timely decisions and organizational reputation.'},
            {'type': 'pulse_moment', 'text': 'The patient may not know your name. But your work may touch their life.'},
        ],
        'voice_over': 'Whether you work in the field, factory, R&D laboratory, office, warehouse or support function, your work contributes to the chain through which Pulse creates value for patients.',
        'interaction_type': 'content',
        'estimated_seconds': 75,
    },
    {
        'screen_number': 26,
        'section': 'application',
        'title': 'Professionalism Detective',
        'on_screen_text': [
            {'type': 'text', 'text': 'Classify each workplace situation. (Colour is supported by text labels for accessibility.)'},
        ],
        'interaction_type': 'classification',
        'interaction_payload': {
            'prompt': 'Classify each situation.',
            'buckets': ['Professional', 'Needs Improvement', 'Unprofessional'],
            'items': [
                {'text': 'An employee arrives late to a meeting and quietly joins without acknowledging the delay.', 'answer': 'Needs Improvement',
                 'feedback': 'Acknowledging the delay respects the time others have already invested.'},
                {'text': 'An employee discovers an error in their report and informs the manager immediately.', 'answer': 'Professional',
                 'feedback': 'Immediate transparency enables early correction and builds trust.'},
                {'text': 'An employee disagrees with a colleague and criticizes the person publicly.', 'answer': 'Unprofessional',
                 'feedback': 'Criticism targets behaviour in private; public attacks target the person.'},
                {'text': 'An employee receives feedback and asks: "What should I do differently next time?"', 'answer': 'Professional',
                 'feedback': 'Turning feedback into a forward-looking action is a hallmark of professionalism.'},
                {'text': 'An employee forwards confidential information to a personal group for convenience.', 'answer': 'Unprofessional',
                 'feedback': 'Confidential information stays in approved channels — no exceptions for convenience.'},
                {'text': 'An employee cannot meet a deadline and informs the stakeholder before the deadline with a revised plan.', 'answer': 'Professional',
                 'feedback': 'Early, solution-oriented communication converts a slip into a managed commitment.'},
            ],
        },
        'estimated_seconds': 210,
    },
    {
        'screen_number': 27,
        'section': 'application',
        'title': 'A Day in the Life of a Pulse Professional',
        'on_screen_text': [
            {'type': 'scenario', 'text': 'You are responsible for an important cross-functional project. It is Monday morning. You have: a customer escalation, a pending report, a team member seeking help, a meeting in 20 minutes, an email requiring sensitive information, and a missed commitment from another department. You have limited time. Make the best professional decision at each stage.'},
        ],
        'interaction_type': 'simulation',
        'interaction_payload': {
            'prompt': 'Five decisions. Choose the best professional response at each stage.',
            'sequential': True,
            'stages': [
                {
                    'title': 'Decision 1 — The customer escalation arrives.',
                    'options': [
                        {'key': 'A', 'text': 'Forward it immediately.', 'correct': False,
                         'feedback': 'Forwarding without understanding can pass the wrong message and delays the customer\'s resolution.'},
                        {'key': 'B', 'text': 'Understand the issue and determine the immediate customer need.', 'correct': True,
                         'feedback': 'Understand first — then route with complete context. The customer feels the difference.'},
                        {'key': 'C', 'text': 'Wait until after the meeting.', 'correct': False,
                         'feedback': 'Customer escalations lose trust every minute they sit unacknowledged.'},
                    ],
                },
                {
                    'title': 'Decision 2 — A colleague has not delivered information you need.',
                    'options': [
                        {'key': 'A', 'text': 'Blame them in the group chat.', 'correct': False,
                         'feedback': 'Public blame damages the relationship you will need again tomorrow.'},
                        {'key': 'B', 'text': 'Contact them, understand the issue and agree on a revised timeline.', 'correct': True,
                         'feedback': 'Direct, respectful problem-solving keeps the work and the relationship moving.'},
                        {'key': 'C', 'text': 'Ignore it.', 'correct': False,
                         'feedback': 'Ignoring delays your project and leaves the colleague unaware of the impact.'},
                    ],
                },
                {
                    'title': 'Decision 3 — The email asks you to share sensitive information externally.',
                    'options': [
                        {'key': 'A', 'text': 'Send it immediately because the request is urgent.', 'correct': False,
                         'feedback': 'Urgency never overrides confidentiality — a leak is irreversible.'},
                        {'key': 'B', 'text': 'Verify authorization and use the approved channel.', 'correct': True,
                         'feedback': 'Verification and approved channels protect the organization and you.'},
                        {'key': 'C', 'text': 'Send it from your personal email.', 'correct': False,
                         'feedback': 'Personal channels for company data are a serious compliance breach.'},
                    ],
                },
                {
                    'title': 'Decision 4 — You realize your own report contains an error.',
                    'options': [
                        {'key': 'A', 'text': 'Hide it.', 'correct': False,
                         'feedback': 'Hidden errors compound — and being discovered later costs far more trust.'},
                        {'key': 'B', 'text': 'Correct it and inform the relevant stakeholder if the error has implications.', 'correct': True,
                         'feedback': 'Transparency plus correction is the professional standard.'},
                        {'key': 'C', 'text': 'Wait until someone notices.', 'correct': False,
                         'feedback': 'Decisions may already be made on flawed numbers — correct proactively.'},
                    ],
                },
                {
                    'title': 'Decision 5 — Your meeting starts. You are still dealing with the customer issue.',
                    'options': [
                        {'key': 'A', 'text': 'Join but remain distracted.', 'correct': False,
                         'feedback': 'Half-presence shortchanges both the meeting and the customer.'},
                        {'key': 'B', 'text': 'Communicate the conflict appropriately, prioritize the customer issue and ensure the meeting receives the required support.', 'correct': True,
                         'feedback': 'Transparent prioritization with coverage for the meeting is professional judgement.'},
                        {'key': 'C', 'text': 'Simply don\'t attend.', 'correct': False,
                         'feedback': 'Silent absence disrupts everyone — communicate and delegate instead.'},
                    ],
                },
            ],
        },
        'estimated_seconds': 300,
    },
    {
        'screen_number': 28,
        'section': 'application',
        'title': 'How Professional Are Your Decisions?',
        'on_screen_text': [
            {'type': 'text', 'text': 'Based on the scenario decisions, here is your current learning profile across seven dimensions: Reliability, Respect, Communication, Ownership, Judgement, Learning Mindset and Digital Professionalism.'},
            {'type': 'note', 'text': 'This is a learning diagnostic — not a formal employee performance rating. Language: "Your current learning profile."'},
        ],
        'interaction_type': 'content',
        'is_mandatory': False,
        'estimated_seconds': 60,
    },
    {
        'screen_number': 29,
        'section': 'assessment',
        'title': 'Module Assessment',
        'on_screen_text': [
            {'type': 'text', 'text': '10 questions · Randomized · Scenario-based · Passing score 80% · Retry permitted. Completion criteria: all mandatory screens and interactions completed, assessment attempted with ≥80%, required activities done. The 7-Day Challenge is a post-module activity and is not required for module completion.'},
        ],
        'interaction_type': 'assessment',
        'estimated_seconds': 600,
    },
    {
        'screen_number': 30,
        'section': 'closure',
        'title': 'Look in the Mirror',
        'on_screen_text': [
            {'type': 'text', 'text': 'Complete these statements: One professional behaviour I already demonstrate well is… One behaviour I need to strengthen is… One person whose professionalism I admire is… One change I will make from tomorrow is… One way I will represent Pulse more professionally is…'},
        ],
        'interaction_type': 'content',
        'is_mandatory': False,
        'estimated_seconds': 90,
        # Reflection storage intentionally deferred per scope decision.
    },
    {
        'screen_number': 31,
        'section': 'challenge',
        'title': 'Your 7-Day Professionalism in Practice Challenge',
        'on_screen_text': [
            {'type': 'text', 'text': 'This is a post-learning workplace application challenge. It is not a prerequisite for completing Module 1. The challenge stays available in your Learning Passport for the next seven working days. Complete one action per day and check it off — +10 points per day, maximum 70.'},
            {'type': 'checklist', 'items': [
                'Day 1 — Be exceptionally punctual.',
                'Day 2 — Practise active listening.',
                'Day 3 — Complete one commitment ahead of time.',
                'Day 4 — Give someone constructive appreciation.',
                'Day 5 — Identify and correct one process or work-quality issue.',
                'Day 6 — Learn one new thing relevant to your role.',
                'Day 7 — Ask a colleague: "What is one thing I could do to be more effective professionally?"',
            ]},
        ],
        'interaction_type': 'challenge',
        'interaction_payload': {
            'days': [
                {'day': 1, 'title': 'Be exceptionally punctual.'},
                {'day': 2, 'title': 'Practise active listening.'},
                {'day': 3, 'title': 'Complete one commitment ahead of time.'},
                {'day': 4, 'title': 'Give someone constructive appreciation.'},
                {'day': 5, 'title': 'Identify and correct one process or work-quality issue.'},
                {'day': 6, 'title': 'Learn one new thing relevant to your role.'},
                {'day': 7, 'title': 'Ask a colleague: "What is one thing I could do to be more effective professionally?"'},
            ],
        },
        'is_mandatory': False,
        'estimated_seconds': 120,
    },
    {
        'screen_number': 32,
        'section': 'commitment',
        'title': 'My Professional Excellence Commitment',
        'on_screen_text': [
            {'type': 'text', 'text': 'I commit to being a trusted professional. I will communicate respectfully, honour my commitments, take ownership, learn continuously, treat others with dignity, use organizational resources responsibly, act with integrity, and represent Pulse Pharmaceuticals with professionalism and pride — remembering that the quality of my work can ultimately make a difference to patients.'},
        ],
        'interaction_type': 'content',
        'is_mandatory': False,
        'estimated_seconds': 60,
        # Commitment capture intentionally deferred per scope decision.
    },
    {
        'screen_number': 33,
        'section': 'closure',
        'title': 'Professionalism Is Your Signature',
        'on_screen_text': [
            {'type': 'text', 'text': 'You may forget some of the content of this module. But remember these seven behaviours: Be Prepared · Be Respectful · Be Reliable · Take Ownership · Keep Learning · Act with Integrity · Represent Pulse with Pride.'},
            {'type': 'pulse_moment', 'text': 'Professionalism is not a title. It is a behaviour.'},
        ],
        'interaction_type': 'content',
        'estimated_seconds': 60,
    },
    {
        'screen_number': 34,
        'section': 'closure',
        'title': 'The Pulse Professional',
        'on_screen_text': [
            {'type': 'text', 'text': 'A Pulse professional: Thinks professionally. Communicates respectfully. Acts ethically. Learns continuously. Collaborates constructively. Delivers reliably. Creates value. Represents Pulse with pride.'},
        ],
        'interaction_type': 'content',
        'estimated_seconds': 45,
    },
    {
        'screen_number': 35,
        'section': 'completion',
        'title': 'Congratulations!',
        'on_screen_text': [
            {'type': 'text', 'text': 'You have completed Module 1 — Professionalism at the Workplace. Your learning journey continues. Your next module: Module 2 — Effective Communication & Interpersonal Excellence. "Communicate to Connect, Influence and Inspire."'},
        ],
        'interaction_type': 'completion',
        'estimated_seconds': 30,
    },
]

# ─── Assessment bank (§O — 10 questions, §17 rich feedback) ──────────────────

QUESTIONS = [
    {
        'question_type': 'knowledge',
        'question_text': 'Which statement best describes professionalism?',
        'options': [
            {'key': 'A', 'text': 'Following rules only when someone is watching.'},
            {'key': 'B', 'text': 'Demonstrating consistent professional standards in behaviour, communication and work.'},
            {'key': 'C', 'text': 'Having strong technical knowledge.'},
            {'key': 'D', 'text': 'Dressing formally every day.'},
        ],
        'correct_option': 'B',
        'feedback_why': 'Professionalism is broader than appearance or technical competence. It is reflected in consistent behaviour, reliability, respect, integrity and commitment.',
        'feedback_better': 'Aim for consistency: the same standards whether observed or not.',
    },
    {
        'question_type': 'application',
        'question_text': 'You realize you cannot meet an agreed deadline. What should you do?',
        'options': [
            {'key': 'A', 'text': 'Wait until the deadline passes.'},
            {'key': 'B', 'text': 'Blame the dependency.'},
            {'key': 'C', 'text': 'Inform the stakeholder early and propose a realistic plan.'},
            {'key': 'D', 'text': 'Ignore the commitment.'},
        ],
        'correct_option': 'C',
        'feedback_why': 'Early communication lets stakeholders adjust plans — it converts a delay into a managed commitment.',
        'feedback_better': 'Flag risks as soon as they appear, with a revised, realistic timeline.',
    },
    {
        'question_type': 'situational_judgement',
        'question_text': 'Which behaviour demonstrates respect?',
        'options': [
            {'key': 'A', 'text': 'Interrupting when you disagree.'},
            {'key': 'B', 'text': 'Correcting someone publicly.'},
            {'key': 'C', 'text': 'Listening and disagreeing constructively.'},
            {'key': 'D', 'text': 'Ignoring junior employees.'},
        ],
        'correct_option': 'C',
        'feedback_why': 'Respect means the freedom to disagree without demeaning people. Constructive disagreement strengthens decisions and relationships.',
        'feedback_better': 'Acknowledge the point, present your view with reasons, and invite dialogue.',
    },
    {
        'question_type': 'application',
        'question_text': 'Which is an example of digital professionalism?',
        'options': [
            {'key': 'A', 'text': 'Forwarding confidential information for convenience.'},
            {'key': 'B', 'text': 'Using informal language in every business communication.'},
            {'key': 'C', 'text': 'Verifying information before sharing it and using approved channels.'},
            {'key': 'D', 'text': 'Responding only when convenient.'},
        ],
        'correct_option': 'C',
        'feedback_why': 'Digital communication is still human communication — verification and approved channels protect trust and confidentiality.',
        'feedback_better': 'Assume everything written may be seen by others; verify before you share.',
    },
    {
        'question_type': 'decision',
        'question_text': 'What should you do when you make a significant mistake?',
        'options': [
            {'key': 'A', 'text': 'Hide it.'},
            {'key': 'B', 'text': 'Blame another person.'},
            {'key': 'C', 'text': 'Acknowledge it, correct it and learn from it.'},
            {'key': 'D', 'text': 'Wait for someone else to discover it.'},
        ],
        'correct_option': 'C',
        'feedback_why': 'Acknowledgement plus correction preserves trust; hiding or blaming compounds the damage.',
        'feedback_better': 'Follow the five steps: acknowledge, understand, correct, prevent, learn.',
    },
    {
        'question_type': 'situational_judgement',
        'question_text': 'Your manager gives you critical feedback. What is the most professional response?',
        'options': [
            {'key': 'A', 'text': 'Become defensive.'},
            {'key': 'B', 'text': 'Ignore it.'},
            {'key': 'C', 'text': 'Ask for specific examples and identify how you can improve.'},
            {'key': 'D', 'text': 'Complain to colleagues.'},
        ],
        'correct_option': 'C',
        'feedback_why': 'Feedback is data for growth — asking for specifics converts criticism into an improvement plan.',
        'feedback_better': '"Thank you — could you share an example so I can correct it going forward?"',
    },
    {
        'question_type': 'knowledge',
        'question_text': 'Which behaviour best reflects the MD\'s philosophy?',
        'options': [
            {'key': 'A', 'text': 'Explain why something cannot be done.'},
            {'key': 'B', 'text': 'Focus on the problem and wait for instructions.'},
            {'key': 'C', 'text': 'Focus on solutions and create value.'},
            {'key': 'D', 'text': 'Avoid taking responsibility.'},
        ],
        'correct_option': 'C',
        'feedback_why': 'The MD values people who focus on solutions rather than problems, think big and create value for customers.',
        'feedback_better': 'Bring a recommended solution with your problem — measure success through impact.',
    },
    {
        'question_type': 'application',
        'question_text': 'An employee doesn\'t know how to use a new digital system. What demonstrates professionalism?',
        'options': [
            {'key': 'A', 'text': 'Reject the system.'},
            {'key': 'B', 'text': 'Continue using old processes secretly.'},
            {'key': 'C', 'text': 'Learn the system and seek support where necessary.'},
            {'key': 'D', 'text': 'Tell the organization the technology is impossible to use.'},
        ],
        'correct_option': 'C',
        'feedback_why': 'Do not make personal limitations the organization\'s limitations — treat capability gaps as learning opportunities.',
        'feedback_better': 'Ask for training, practise, and then evaluate the system on its merits.',
    },
    {
        'question_type': 'knowledge',
        'question_text': 'Which statement about punctuality is most accurate?',
        'options': [
            {'key': 'A', 'text': 'It matters only for senior employees.'},
            {'key': 'B', 'text': 'It demonstrates respect and reliability.'},
            {'key': 'C', 'text': 'It is unimportant if work gets completed.'},
            {'key': 'D', 'text': 'It matters only for physical meetings.'},
        ],
        'correct_option': 'B',
        'feedback_why': 'Punctuality communicates reliability and respect for other people\'s time — everywhere, for everyone.',
        'feedback_better': 'Treat every start time — virtual or physical — as a commitment.',
    },
    {
        'question_type': 'decision',
        'question_text': 'Which behaviour best demonstrates ownership?',
        'options': [
            {'key': 'A', 'text': '"This isn\'t my responsibility."'},
            {'key': 'B', 'text': '"Someone else should solve it."'},
            {'key': 'C', 'text': '"Let me understand the issue and see how I can contribute to solving it."'},
            {'key': 'D', 'text': '"I will wait for instructions."'},
        ],
        'correct_option': 'C',
        'feedback_why': 'Ownership starts with understanding and contribution — not with deferring or deflecting.',
        'feedback_better': 'Ask what you can control, act on it, and coordinate the rest.',
    },
]
