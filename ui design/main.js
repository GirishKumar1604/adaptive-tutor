document.addEventListener('DOMContentLoaded', () => {
  // Elements
  const viewHero = document.getElementById('view-hero');
  const viewWorkspace = document.getElementById('view-workspace');
  
  const topicInput = document.getElementById('topic-input');
  const btnStartLearning = document.getElementById('btn-start-learning');
  
  const displayTopic = document.getElementById('display-topic');
  const chatTopic = document.getElementById('chat-topic');
  
  // Tabs
  const btnTabCoach = document.getElementById('btn-tab-coach');
  const btnTabQuiz = document.getElementById('btn-tab-quiz');
  const tabsIndicator = document.getElementById('tabs-indicator');
  
  const tabCoach = document.getElementById('tab-coach');
  const tabQuiz = document.getElementById('tab-quiz');

  // Hide workspace initially
  viewWorkspace.classList.add('hidden');

  // Transition to Workspace
  const startLearningFlow = () => {
    const topic = topicInput.value.trim() || 'Machine Learning Basics';
    displayTopic.textContent = topic;
    chatTopic.textContent = topic;
    
    // Hide hero with scale and fade out
    viewHero.style.transition = 'opacity 400ms cubic-bezier(0.23, 1, 0.32, 1), transform 400ms cubic-bezier(0.23, 1, 0.32, 1)';
    viewHero.style.opacity = '0';
    viewHero.style.transform = 'scale(0.96)';
    
    setTimeout(() => {
      viewHero.classList.add('hidden');
      viewWorkspace.classList.remove('hidden');
      
      // Reset scroll position
      window.scrollTo(0, 0);
    }, 400);
  };

  btnStartLearning.addEventListener('click', startLearningFlow);
  
  topicInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') startLearningFlow();
  });

  // Tab switching with sleek spring animation
  const switchTab = (target) => {
    if (target === 'coach') {
      btnTabCoach.classList.add('active');
      btnTabQuiz.classList.remove('active');
      
      // Moving indicator to first tab
      tabsIndicator.style.transform = 'translateX(0)';
      
      tabCoach.classList.add('active');
      tabQuiz.classList.remove('active');
    } else {
      btnTabQuiz.classList.add('active');
      btnTabCoach.classList.remove('active');
      
      // Calculate translation based on gap (translateX maps to the width of the active tab)
      // Since width is 50% - 16px, we need to translate by 100% + gap (8px) 
      // A simple way is to use calc
      tabsIndicator.style.transform = 'translateX(calc(100% + 8px))';
      
      tabQuiz.classList.add('active');
      tabCoach.classList.remove('active');
    }
  };

  btnTabCoach.addEventListener('click', () => switchTab('coach'));
  btnTabQuiz.addEventListener('click', () => switchTab('quiz'));
});
